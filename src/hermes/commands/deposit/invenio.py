# SPDX-FileCopyrightText: 2023 Helmholtz-Zentrum Dresden-Rossendorf (HZDR)
#
# SPDX-License-Identifier: Apache-2.0

# SPDX-FileContributor: David Pape
# SPDX-FileContributor: Oliver Bertuch
# SPDX-FileContributor: Michael Meinel

import json
import logging
import typing as t
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

import click
import requests

from hermes import config
from hermes.commands.deposit.error import DepositionUnauthorizedError
from hermes.error import MisconfigurationError
from hermes.model.context import CodeMetaContext
from hermes.model.path import ContextPath
from hermes.utils import hermes_user_agent


# TODO: Move common functionality into base class
# TODO: Add type annotations to aid subclass implementation
class InvenioDepositPlugin:
    default_licenses_api_path = "api/licenses"
    default_communities_api_path = "api/communities"
    default_depositions_api_path = "api/deposit/depositions"

    def __init__(self, click_ctx: click.Context, ctx: CodeMetaContext) -> None:
        self.click_ctx = click_ctx
        self.ctx = ctx

    def run(self):
        # TODO: Decide here which of initial/new/... to run?
        steps = [
            "prepare",
            "map",
            "create_initial_version",
            "create_new_version",
            "update_metadata",
            "delete_artifacts",
            "upload_artifacts",
            "publish",
        ]

        for step in steps:
            getattr(self, step)()


    def prepare(self):
        """Prepare the deposition on an Invenio-based platform.

        In this function we do the following:

        - resolve the latest published version of this publication (if any)
        - check whether the current version (given in the CodeMeta) was already published
        - check whether we have a valid license identifier (if any)
        - check wether the communities are valid (if configured)
        - check access modalities (access right, access conditions, embargo data, existence
        of license)
        - check whether required configuration options are present
        - check whether an auth token is given
        - update ``ctx`` with metadata collected during the checks
        """

        if not self.click_ctx.params["auth_token"]:
            raise DepositionUnauthorizedError("No auth token given for deposition platform")

        invenio_path = ContextPath.parse("deposit.invenio")
        invenio_config = config.get("deposit").get("invenio", {})
        rec_id, rec_meta = _resolve_latest_invenio_id(self.ctx)

        version = self.ctx["codemeta"].get("version")
        if rec_meta and (version == rec_meta.get("version")):
            raise ValueError(f"Version {version} already deposited.")

        self.ctx.update(invenio_path['latestRecord'], {'id': rec_id, 'metadata': rec_meta})

        site_url = invenio_config.get("site_url")
        if site_url is None:
            raise MisconfigurationError("deposit.invenio.site_url is not configured")

        licenses_api_path = invenio_config.get("api_paths", {}).get(
            "licenses", self.default_licenses_api_path
        )
        licenses_api_url = f"{site_url}/{licenses_api_path}"
        license = _get_license_identifier(self.ctx, licenses_api_url)
        self.ctx.update(invenio_path["license"], license)

        communities_api_path = invenio_config.get("api_paths", {}).get(
            "communities", self.default_communities_api_path
        )
        communities_api_url = f"{site_url}/{communities_api_path}"
        communities = _get_community_identifiers(self.ctx, communities_api_url)
        self.ctx.update(invenio_path["communities"], communities)

        access_right, embargo_date, access_conditions = _get_access_modalities(license)
        self.ctx.update(invenio_path["access_right"], access_right)
        self.ctx.update(invenio_path["embargo_date"], embargo_date)
        self.ctx.update(invenio_path["access_conditions"], access_conditions)


    def map(self):
        """Map the harvested metadata onto the Invenio schema."""

        deposition_metadata = _codemeta_to_invenio_deposition(self.ctx)

        metadata_path = ContextPath.parse("deposit.invenio.depositionMetadata")
        self.ctx.update(metadata_path, deposition_metadata)

        # Store a snapshot of the mapped data within the cache, useful for analysis, debugging, etc
        with open(self.ctx.get_cache("deposit", "invenio", create=True), 'w') as invenio_json:
            json.dump(deposition_metadata, invenio_json, indent='  ')


    def create_initial_version(self):
        """Create an initial version of a publication.

        If a previous publication exists, this function does nothing, leaving the work for
        :func:`create_new_version`.
        """

        invenio_path = ContextPath.parse("deposit.invenio")
        invenio_ctx = self.ctx[invenio_path]
        latest_record_id = invenio_ctx.get("latestRecord", {}).get("id")

        if latest_record_id is not None:
            # A previous version exists. This means that we need to create a new version in
            # the next step. Thus, there is nothing to do here.
            return

        if not self.click_ctx.params['initial']:
            raise RuntimeError("Please use `--initial` to make an initial deposition.")

        _log = logging.getLogger("cli.deposit.invenio")

        invenio_config = config.get("deposit").get("invenio", {})
        site_url = invenio_config["site_url"]
        depositions_api_path = invenio_config.get("api_paths", {}).get(
            "depositions", self.default_depositions_api_path
        )

        deposition_metadata = invenio_ctx["depositionMetadata"]

        deposit_url = f"{site_url}/{depositions_api_path}"
        response = requests.post(
            deposit_url,
            json={"metadata": deposition_metadata},
            headers={
                "User-Agent": hermes_user_agent,
                "Authorization": f"Bearer {self.click_ctx.params['auth_token']}",
            }
        )

        if not response.ok:
            raise RuntimeError(f"Could not create initial deposit {deposit_url!r}")

        deposit = response.json()
        _log.debug("Created initial version deposit: %s", deposit["links"]["html"])
        with open(self.ctx.get_cache('deposit', 'deposit', create=True), 'w') as deposit_file:
            json.dump(deposit, deposit_file, indent=4)

        self.ctx.update(invenio_path["links"]["bucket"], deposit["links"]["bucket"])
        self.ctx.update(invenio_path["links"]["publish"], deposit["links"]["publish"])


    def create_new_version(self):
        """Create a new version of an existing publication.

        If no previous publication exists, this function does nothing because
        :func:`create_initial_version` will have done the work.
        """

        invenio_path = ContextPath.parse("deposit.invenio")
        invenio_ctx = self.ctx[invenio_path]
        latest_record_id = invenio_ctx.get("latestRecord", {}).get("id")

        if latest_record_id is None:
            # No previous version exists. This means that an initial version was created in
            # the previous step. Thus, there is nothing to do here.
            return

        session = requests.Session()
        session.headers = {
            "User-Agent": hermes_user_agent,
            "Authorization": f"Bearer {self.click_ctx.params['auth_token']}",
        }

        invenio_config = config.get("deposit").get("invenio", {})
        site_url = invenio_config["site_url"]
        depositions_api_path = invenio_config.get("api_paths", {}).get(
            "depositions", self.default_depositions_api_path
        )

        # Get current deposit
        deposit_url = f"{site_url}/{depositions_api_path}/{latest_record_id}"
        response = session.get(deposit_url)
        if not response.ok:
            raise RuntimeError(f"Failed to get current deposit {deposit_url!r}")

        # Create a new version using the newversion action
        deposit_url = response.json()["links"]["newversion"]
        response = session.post(deposit_url)
        if not response.ok:
            raise RuntimeError(f"Could not create new version deposit {deposit_url!r}")

        # Store link to latest draft to be used in :func:`update_metadata`.
        old_deposit = response.json()
        self.ctx.update(invenio_path["links"]["latestDraft"], old_deposit['links']['latest_draft'])


    def update_metadata(self):
        """Update the metadata of a draft.

        If no draft is found in the context, it is assumed that no metadata has to be
        updated (e.g. because an initial version was created already containing the
        metadata).
        """

        invenio_path = ContextPath.parse("deposit.invenio")
        invenio_ctx = self.ctx[invenio_path]
        draft_url = invenio_ctx.get("links", {}).get("latestDraft")

        if draft_url is None:
            return

        _log = logging.getLogger("cli.deposit.invenio")

        deposition_metadata = invenio_ctx["depositionMetadata"]

        response = requests.put(
            draft_url,
            json={"metadata": deposition_metadata},
            headers={
                "User-Agent": hermes_user_agent,
                "Authorization": f"Bearer {self.click_ctx.params['auth_token']}",
            }
        )

        if not response.ok:
            raise RuntimeError(f"Could not update metadata of draft {draft_url!r}")

        deposit = response.json()
        _log.debug("Created new version deposit: %s", deposit["links"]["html"])
        with open(self.ctx.get_cache('deposit', 'deposit', create=True), 'w') as deposit_file:
            json.dump(deposit, deposit_file, indent=4)

        self.ctx.update(invenio_path["links"]["bucket"], deposit["links"]["bucket"])
        self.ctx.update(invenio_path["links"]["publish"], deposit["links"]["publish"])


    def delete_artifacts(self):
        """Delete existing file artifacts.

        This is done so that files which existed in an earlier publication but don't exist
        any more, are removed. Otherwise they would cause an error because the didn't change
        between versions.
        """
        # TODO: This needs to be implemented!
        pass


    def upload_artifacts(self):
        """Upload file artifacts to the deposit.

        We'll use the bucket API rather than the files API as it supports file sizes above
        100MB. The URL to the bucket of the deposit is taken from the context at
        ``deposit.invenio.links.bucket``.
        """

        bucket_url_path = ContextPath.parse("deposit.invenio.links.bucket")
        bucket_url = self.ctx[bucket_url_path]

        session = requests.Session()
        session.headers = {
            "User-Agent": hermes_user_agent,
            "Authorization": f"Bearer {self.click_ctx.params['auth_token']}",
        }

        files: list[click.Path] = self.click_ctx.params["file"]
        for path_arg in files:
            path = Path(path_arg)

            # This should not happen, as Click shall not accept dirs as arguments already. Zero trust anyway.
            if not path.is_file():
                raise ValueError("Any given argument to be included in the deposit must be a file.")

            with open(path, "rb") as file_content:
                response = session.put(
                    f"{bucket_url}/{path.name}",
                    data=file_content
                )
                if not response.ok:
                    raise RuntimeError(f"Could not upload file {path.name!r} into bucket {bucket_url!r}")

            # This can potentially be used to verify the checksum
            # file_resource = response.json()


    def publish(self):
        """Publish the deposited record.

        This is done by doing a POST request to the publication URL stored in the context at
        ``deposit.invenio.links.publish``.
        """

        _log = logging.getLogger("cli.deposit.invenio")

        publish_url_path = ContextPath.parse("deposit.invenio.links.publish")
        publish_url = self.ctx[publish_url_path]

        response = requests.post(
            publish_url,
            headers={
                "User-Agent": hermes_user_agent,
                "Authorization": f"Bearer {self.click_ctx.params['auth_token']}"
            }
        )

        if not response.ok:
            _log.debug(response.text)
            raise RuntimeError(f"Could not publish deposit via {publish_url!r}")

        record = response.json()
        _log.info("Published record: %s", record["links"]["record_html"])


def _resolve_latest_invenio_id(ctx: CodeMetaContext) -> t.Tuple[str, dict]:
    """
    Using the given configuration and metadata, figure out the latest record id.

    If a record id is present as configuration ``deposit.invenio.record_id`` this one will be used to identify the
    latest version of the record. Otherwise, if there is a doi present (either as configuration with key
    ``deposit.invenio.doi``  or as a codemeta identifier, the DOI will be used to resolve the base record id.

    Anyway, the record id will always be used to resolve the latest version.

    If any of the resolution steps fail or produce an unexpected result, a ValueError will be thrown.

    :param ctx: The context for which the record id should be resolved.
    :return: The Invenio record id and the metadata of the record
    """

    invenio_config = config.get('deposit').get('invenio', {})
    site_url = invenio_config.get('site_url')
    if site_url is None:
        raise MisconfigurationError("deposit.invenio.site_url is not configured")

    # Check if we configured an Invenio record ID (of the concept...)
    record_id = invenio_config.get('record_id')
    if record_id is None:
        doi = invenio_config.get('doi')
        if doi is None:
            try:
                # TODO: There might be more semantic in the codemeta.identifier... (also see schema.org)
                identifier = ctx['codemeta.identifier']
                if identifier.startswith('https://doi.org/'):
                    doi = identifier[16:]
                elif identifier.startswith('http://dx.doi.org/'):
                    doi = identifier[18:]
            except KeyError:
                pass

        if doi is not None:
            # If we got a DOI, resolve it (using doi.org) into a Invenio URL ... and extract the record id.
            record_id = _invenio_resolve_doi(site_url, doi)

    if record_id is not None:
        # If we got a record id by now, resolve it using the Invenio API to the latests record.
        return _invenio_resolve_record_id(site_url, record_id)

    return None, {}


def _invenio_resolve_doi(site_url, doi) -> str:
    """
    Resolve an DOI to a Invenio URL and extract the record id.

    :param site_url: Root URL for the Invenio instance to use.
    :param doi: The DOI to be resolved (only the identifier *without* the ``https://doi.org/`` prefix).
    :return: The record ID on the respective instance.
    """

    res = requests.get(f'https://doi.org/{doi}')

    # This is a mean hack due to DataCite answering a 404 with a 200 status
    if res.url == 'https://datacite.org/404.html':
        raise ValueError(f"Invalid DOI: {doi}")

    # Ensure the resolved record is on the correct instance
    if not res.url.startswith(site_url):
        raise ValueError(f"{res.url} is not on configured host {site_url}.")

    # Extract the record id as last part of the URL path
    page_url = urlparse(res.url)
    *_, record_id = page_url.path.split('/')
    return record_id


def _invenio_resolve_record_id(site_url: str, record_id: str) -> t.Tuple[str, dict]:
    """
    Find the latest version of a given record.

    :param site_url: Root URL for the Invenio instance to use.
    :param record_id: The record that sould be resolved.
    :return: The record id of the latest version for the requested record.
    """
    res = requests.get(f"{site_url}/api/records/{record_id}")
    if res.status_code != 200:
        raise ValueError(f"Could not retrieve record from {res.url}: {res.text}")

    res_json = res.json()
    res = requests.get(res_json['links']['latest'])
    if res.status_code != 200:
        raise ValueError(f"Could not retrieve record from {res.url}: {res.text}")

    res_json = res.json()
    return res_json['id'], res_json['metadata']


def _codemeta_to_invenio_deposition(ctx: CodeMetaContext) -> dict:
    """The mapping logic.

    Functionality similar to this exists in the ``convert_codemeta`` package which uses
    the crosswalk tables to do the mapping:

    .. code-block:: python

       invenio_metadata = convert_codemeta.crosswalk(
           metadata, "codemeta", "Zenodo"
       )

    Unfortunately, this doesn't work well with additional metadata in the same dict, so
    it is safer to provide our own implementation.

    Currently, this function handles a lot of cases which we want to be able to
    configure. A simple mapping from one JSON path to another is not enough.

    The metadata expected by Zenodo is described in the `Zenodo Developers guide
    <https://developers.zenodo.org/#representation>`_. Unfortunately, there doesn't seem
    to be a schema one can download in order to validate these metadata. There might be
    differences between Invenio-based platforms.
    """

    metadata = ctx["codemeta"]
    license = ctx["deposit"]["invenio"]["license"]
    communities = ctx["deposit"]["invenio"]["communities"]
    access_right = ctx["deposit"]["invenio"]["access_right"]
    embargo_date = ctx["deposit"]["invenio"]["embargo_date"]
    access_conditions = ctx["deposit"]["invenio"]["access_conditions"]

    creators = [
        # TODO: Distinguish between @type "Person" and others
        {
            k: v for k, v in {
                # TODO: This is ugly
                "affiliation": author.get("affiliation", {"legalName": None}).get("legalName"),
                # Invenio wants "family, given". author.get("name") might not have this format.
                "name": f"{author.get('familyName')}, {author.get('givenName')}"
                if author.get("familyName") and author.get("givenName")
                else author.get("name"),
                # Invenio expects the ORCID without the URL part
                "orcid": author.get("@id", "").replace("https://orcid.org/", "") or None,
            }.items() if v is not None
        }
        for author in metadata["author"]
    ]

    # This is not used at the moment. See comment below in `deposition_metadata` dict.
    contributors = [  # noqa: F841
        # TODO: Distinguish between @type "Person" and others
        {
            k: v for k, v in {
                # TODO: This is ugly
                "affiliation": contributor.get("affiliation", {"legalName": None}).get("legalName"),
                # Invenio wants "family, given". contributor.get("name") might not have this format.
                "name": f"{contributor.get('familyName')}, {contributor.get('givenName')}"
                if contributor.get("familyName") and contributor.get("givenName")
                else contributor.get("name"),
                # Invenio expects the ORCID without the URL part
                "orcid": contributor.get("@id", "").replace("https://orcid.org/", "") or None,
                # TODO: Many possibilities here. Get from config
                "type": "ProjectMember",
            }.items() if v is not None
        }
        # TODO: Filtering out "GitHub" should be done elsewhere
        for contributor in metadata["contributor"] if contributor.get("name") != "GitHub"
    ]

    # TODO: Use the fields currently set to `None`.
    # Some more fields are available but they most likely don't relate to software
    # publications targeted by hermes.
    deposition_metadata = {k: v for k, v in {
        # If upload_type is "publication"/"image", a publication_type/image_type must be
        # specified. Since hermes targets software publications, this can be ignored and
        # upload_type can be hard-coded to "software".
        # TODO: Make this a constant maybe.
        "upload_type": "software",
        # IS0 8601-formatted date
        # TODO: Maybe we want a different date? Then make this configurable. If not,
        # this can be removed as it defaults to today.
        "publication_date": date.today().isoformat(),
        "title": metadata["name"],
        "creators": creators,
        # TODO: Use a real description here. Possible sources could be
        # `tool.poetry.description` from pyproject.toml or `abstract` from
        # CITATION.cff. This should then be stored in codemeta description field.
        "description": metadata["name"],
        "access_right": access_right,
        "license": license,
        "embargo_date": embargo_date,
        "access_conditions": access_conditions,
        # TODO: If a publisher already has assigned a DOI to the files we want to
        # upload, it should be used here. In this case, Invenio will not give us a new
        # one. Set "prereserve_doi" accordingly.
        "doi": None,
        # This prereserves a DOI that can then be added to the files before publishing
        # them.
        # TODO: Use the DOI we get back from this.
        "prereserve_doi": True,
        # TODO: A good source for this could be `tool.poetry.keywords` in pyproject.toml.
        "keywords": None,
        "notes": None,
        "related_identifiers": None,
        # TODO: Use `contributors`. In the case of the hermes workflow itself, the
        # contributors are currently all in `creators` already. So for now, we set this
        # to `None`. Change this when relationship between authors and contributors can
        # be specified in the processing step.
        "contributors": None,
        "references": None,
        "communities": communities,
        "grants": None,
        "subjects": None,
        "version": metadata.get('version'),
    }.items() if v is not None}

    return deposition_metadata


def _get_license_identifier(ctx: CodeMetaContext, license_api_url: str):
    """Get Invenio license representation from CodeMeta.

    The license to use is extracted from the ``license`` field in the
    :class:`CodeMetaContext` and converted into an appropiate license identifier to be
    passed to an Invenio instance.

    A license according to CodeMeta may be a URL (text) or a CreativeWork. This function
    only handles URLs. If a ``license`` field is present in the CodeMeta and it is not
    of type :class:`str`, a :class:`RuntimeError` is raised.

    Invenio instances take a license string which refers to a license identifier.
    Typically, Invenio instances offer licenses from https://opendefinition.org and
    https://spdx.org. However, it is possible to mint PIDs for custom licenses.

    An API endpoint (usually ``/api/licenses``) can be used to check whether a given
    license is supported by the Invenio instance. This function tries to retrieve the
    license by the identifier at the end of the license URL path. If this identifier
    does not exist on the Invenio instance, a :class:`RuntimeError` is raised. If no
    license is given in the CodeMeta, ``None`` is returned.
    """

    license_url = ctx["codemeta"].get("license")

    if license_url is None:
        return None

    if not isinstance(license_url, str):
        raise RuntimeError(
            "The given license in CodeMeta must be of type str. "
            "Licenses of type 'CreativeWork' are not supported."
        )

    parsed_url = urlparse(license_url)
    url_path = parsed_url.path.rstrip("/")
    license_id = url_path.split("/")[-1]

    response = requests.get(
        f"{license_api_url}/{license_id}", headers={"User-Agent": hermes_user_agent}
    )
    if response.status_code == 404:
        raise RuntimeError(f"Not a valid license identifier: {license_id}")
    # Catch other problems
    response.raise_for_status()

    return response.json()["id"]


def _get_community_identifiers(ctx: CodeMetaContext, communities_api_url: str):
    """Get Invenio community identifiers from config.

    This function gets the communities to be used for the deposition on an Invenio-based
    site from the config and checks their validity against the site's API. If one of the
    identifiers can not be found on the site, a :class:`MisconfigurationError` is
    raised.
    """

    communities = config.get("deposit").get("invenio", {}).get("communities")
    if communities is None:
        return None

    session = requests.Session()
    session.headers = {"User-Agent": hermes_user_agent}

    community_ids = []
    for community_id in communities:
        url = f"{communities_api_url}/{community_id}"
        response = session.get(url)
        if response.status_code == 404:
            raise MisconfigurationError(
                f"Not a valid community identifier: {community_id}"
            )
        # Catch other problems
        response.raise_for_status()
        community_ids.append({"identifier": response.json()["id"]})

    return community_ids


def _get_access_modalities(license):
    """Get access right, embargo date and access conditions based on configuration and given license.

    This function implements the rules laid out in the `Zenodo developer documentation
    <https://developers.zenodo.org/#representation>`_:

    - ``access_right`` is a controlled vocabulary
    - embargoed access depositions need an embargo date
    - restricted access depositions need access conditions
    - open and embargoed access depositions need a license
    - closed access depositions have no further requirements

    This function also makes sure that the given embargo date can be parsed as an ISO
    8601 string representation and that the access rights are given as a string.
    """
    invenio_config = config.get("deposit").get("invenio", {})

    access_right = invenio_config.get("access_right")
    if access_right is None:
        raise MisconfigurationError("deposit.invenio.access_right is not configured")

    access_right_options = ["open", "embargoed", "restricted", "closed"]
    if access_right not in access_right_options:
        raise MisconfigurationError(
            "deposition.invenio.access_right must be one of: "
            f"{', '.join(access_right_options)}"
        )

    embargo_date = invenio_config.get("embargo_date")
    if access_right == "embargoed" and embargo_date is None:
        raise MisconfigurationError(
            f"With access_right {access_right}, "
            "deposit.invenio.embargo_date must be configured"
        )

    if embargo_date is not None:
        try:
            datetime.fromisoformat(embargo_date)
        except ValueError:
            raise MisconfigurationError(
                f"Could not parse deposit.invenio.embargo_date {embargo_date!r}. "
                "Must be in ISO 8601 format."
            )

    access_conditions = invenio_config.get("access_conditions")
    if access_right == "restricted" and access_conditions is None:
        raise MisconfigurationError(
            f"With access_right {access_right}, "
            "deposit.invenio.access_conditions must be configured"
        )

    if access_conditions is not None and not isinstance(access_conditions, str):
        raise MisconfigurationError(
            "deposit.invenio.access_conditions must be a string (HTML is allowed)."
        )

    if license is None and access_right in ["open", "embargoed"]:
        raise MisconfigurationError(
            f"With access_right {access_right}, a license is required."
        )

    if access_right == "closed":
        pass

    return access_right, embargo_date, access_conditions
