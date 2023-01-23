# SPDX-FileCopyrightText: 2023 Helmholtz-Zentrum Dresden-Rossendorf (HZDR)
#
# SPDX-License-Identifier: Apache-2.0

# SPDX-FileContributor: David Pape

from datetime import date

import requests
# import jsonschema

from hermes.model.context import CodeMetaContext
from hermes.model.path import ContextPath


def prepare_deposit(ctx: CodeMetaContext):
    """Prepare the Invenio deposit.

    In this case, "prepare" means download the record schema that is required
    by Invenio instances. This is the basis that will be used for metadata
    mapping in the next step.
    """

    invenio_path = ContextPath.parse("deposit.invenio")

    invenio_ctx = ctx[invenio_path]
    # TODO: Get these values from config with reasonable defaults.
    recordSchemaUrl = f"{invenio_ctx['siteUrl']}/{invenio_ctx['recordSchemaPath']}"

    # TODO: cache this download in HERMES cache dir
    # TODO: ensure to use from cache instead of download if not expired (needs config)
    recordSchema = _request_json(recordSchemaUrl)
    ctx.update(invenio_path["requiredSchema"], recordSchema)


def map_metadata(ctx: CodeMetaContext):
    """Map the harvested metadata onto the Invenio schema."""

    deposition_metadata = _codemeta_to_invenio_deposition(ctx["codemeta"])

    # invenio_path = ContextPath.parse("deposit.invenio")

    # invenio_ctx = ctx[invenio_path]
    # target_schema = invenio_ctx["requiredSchema"]

    # TODO: Is it always draft 4 in the case of invenio?
    # validator = jsonschema.Draft4Validator(target_schema)
    # errors = sorted(validator.iter_errors(deposition_metadata), key=lambda e: e.path)

    from pprint import pprint
    pprint(deposition_metadata)


def _request_json(url: str) -> dict:
    """Request an URL and return the JSON response as dict."""

    # TODO: Store a requests.Session in a click_ctx in case we need it more frequently?
    response = requests.get(url)
    response.raise_for_status()
    return response.json()


def _codemeta_to_invenio_deposition(metadata: dict) -> dict:
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
    """

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

    contributors = [
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

    # https://developers.zenodo.org/#representation
    deposition_metadata = {
        "upload_type": "software",
        # IS0 8601-formatted.
        # TODO: Maybe we want a different date? Then make this configurable. If not,
        #  this can be removed as it defaults to today.
        "publication_date": date.today().isoformat(),
        "title": metadata["name"],
        "creators": creators,
        # TODO: Use a real description here
        "description": metadata["name"],
        # TODO: Get from config
        # TODO: Needs some more logic:
        #  Possible options: open, embargoed, restricted, closed
        #  "open" and "restricted" should come with a license; "embargoed" with an
        #  "embargo_date"; restricted with "access_conditions".
        "access_right": "open",
        # This prereserves a DOI that can then be added to the files before publishing
        # them.
        "prereserve_doi": True,
        "contributors": contributors,
        # TODO: Get this from config/codemeta/...
        "license": "Apache-2.0",
        # TODO: More fields are available
    }

    return deposition_metadata
