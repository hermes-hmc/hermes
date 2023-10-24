# SPDX-FileCopyrightText: 2022 German Aerospace Center (DLR)
#
# SPDX-License-Identifier: Apache-2.0

# SPDX-FileContributor: Stephan Druskat
# SPDX-FileContributor: Michael Meinel
# SPDX-FileContributor: David Pape
# SPDX-FileContributor: Oliver Bertuch

import json
import logging
import os
import shutil
from importlib import metadata

import click

from hermes import config
from hermes.commands.deposit.base import BaseDepositPlugin
from hermes.error import MisconfigurationError
from hermes.model.context import HermesContext, HermesHarvestContext, CodeMetaContext
from hermes.model.errors import MergeError
from hermes.model.path import ContextPath


@click.group(invoke_without_command=True)
@click.pass_context
def harvest(click_ctx: click.Context):
    """
    Automatic harvest of metadata
    """
    _log = logging.getLogger('cli.harvest')
    audit_log = logging.getLogger('audit')
    audit_log.info("# Metadata harvesting")

    # Create Hermes context (i.e., all collected metadata for all stages...)
    ctx = HermesContext()

    # Initialize the harvest cache directory here to indicate the step ran
    ctx.init_cache("harvest")

    # Get all harvesters
    harvest_config = config.get("harvest")
    harvester_names = harvest_config.get('from', [ep.name for ep in metadata.entry_points(group='hermes.harvest')])

    for harvester_name in harvester_names:
        harvesters = metadata.entry_points(group='hermes.harvest', name=harvester_name)
        if not harvesters:
            _log.warning("- Harvester %s selected but not found.", harvester_name)
            continue

        harvester, *_ = harvesters
        _log.info("- Running harvester %s", harvester.name)

        _log.debug(". Loading harvester from %s", harvester.value)
        harvest = harvester.load()

        with HermesHarvestContext(ctx, harvester, harvest_config.get(harvester.name, {})) as harvest_ctx:
            harvest(click_ctx, harvest_ctx)
            for _key, ((_value, _tag), *_trace) in harvest_ctx._data.items():
                if any(v != _value and t == _tag for v, t in _trace):
                    raise MergeError(_key, None, _value)

        _log.info('')
    audit_log.info('')


@click.group(invoke_without_command=True)
@click.pass_context
def process(click_ctx: click.Context):
    """
    Process metadata and prepare it for deposition
    """
    _log = logging.getLogger('cli.process')

    audit_log = logging.getLogger('audit')
    audit_log.info("# Metadata processing")

    ctx = CodeMetaContext()

    if not (ctx.hermes_dir / "harvest").exists():
        _log.error("You must run the harvest command before process")
        click_ctx.exit(1)

    # Get all harvesters
    harvest_config = config.get("harvest")
    harvester_names = harvest_config.get('from', [ep.name for ep in metadata.entry_points(group='hermes.harvest')])

    for harvester_name in harvester_names:
        harvesters = metadata.entry_points(group='hermes.harvest', name=harvester_name)
        if not harvesters:
            _log.warning("- Harvester %s selected but not found.", harvester_name)
            continue

        harvester, *_ = harvesters
        audit_log.info("## Process data from %s", harvester.name)

        harvest_context = HermesHarvestContext(ctx, harvester, {})
        try:
            harvest_context.load_cache()
        # when the harvest step ran, but there is no cache file, this is a serious flaw
        except FileNotFoundError:
            _log.warning("No output data from harvester %s found, skipping", harvester.name)
            continue

        preprocessors = metadata.entry_points(group='hermes.preprocess', name=harvester.name)
        for preprocessor in preprocessors:
            _log.debug(". Loading context preprocessor %s", preprocessor.value)
            preprocess = preprocessor.load()

            _log.debug(". Apply preprocessor %s", preprocessor.value)
            preprocess(ctx, harvest_context)

        ctx.merge_from(harvest_context)
        ctx.merge_contexts_from(harvest_context)
        _log.info('')
    audit_log.info('')

    if ctx._errors:
        audit_log.error('!!! warning "Errors during merge"')

        for ep, error in ctx._errors:
            audit_log.info('    - %s: %s', ep.name, error)

    tags_path = ctx.get_cache('process', 'tags', create=True)
    with tags_path.open('w') as tags_file:
        json.dump(ctx.tags, tags_file, indent=2)

    ctx.prepare_codemeta()

    with open(ctx.get_cache("process", ctx.hermes_name, create=True), 'w') as codemeta_file:
        json.dump(ctx._data, codemeta_file, indent=2)

    logging.shutdown()


@click.group(invoke_without_command=True)
@click.pass_context
def curate(click_ctx: click.Context):
    ctx = CodeMetaContext()
    process_output = ctx.hermes_dir / 'process' / (ctx.hermes_name + ".json")

    if not process_output.is_file():
        click.echo("No processed metadata found. Please run `hermes process` before curation.")
        click_ctx.exit(1)

    os.makedirs(ctx.hermes_dir / 'curate', exist_ok=True)
    shutil.copy(process_output, ctx.hermes_dir / 'curate' / (ctx.hermes_name + '.json'))


@click.group(invoke_without_command=True)
@click.option(
    "--initial", is_flag=True, default=False,
    help="Allow initial deposition if no previous version exists in target repository. "
         "Otherwise only an existing, configured upstream record may be updated."
)
@click.option(
    "--auth-token", envvar="HERMES_DEPOSITION_AUTH_TOKEN",
    help="Token used to authenticate the user with the target deposition platform. "
         "Can be passed on the command line or as an environment variable."
)
@click.option(
    "--file", "-f", multiple=True, required=True,
    type=click.Path(exists=True, dir_okay=False, readable=True),
    help="Files to be uploaded on the target deposition platform. "
         "This option may be passed multiple times."
)
@click.pass_context
def deposit(click_ctx: click.Context, initial, auth_token, file):
    """
    Deposit processed (and curated) metadata.
    """
    click.echo("Metadata deposition")
    _log = logging.getLogger("cli.deposit")

    ctx = CodeMetaContext()

    codemeta_file = ctx.get_cache("curate", ctx.hermes_name)
    if not codemeta_file.exists():
        _log.error("You must run the 'curate' command before deposit")
        click_ctx.exit(1)

    # Loading the data into the "codemeta" field is a temporary workaround used because
    # the CodeMetaContext does not provide an update_from method. Eventually we want the
    # the context to contain `{**data}` rather than `{"codemeta": data}`. Then, for
    # additional data, the hermes namespace should be used.
    codemeta_path = ContextPath("codemeta")
    with open(codemeta_file) as codemeta_fh:
        ctx.update(codemeta_path, json.load(codemeta_fh))

    deposit_config = config.get("deposit")

    # TODO: Rename group → ?
    group = "hermes.deposit"
    # TODO: Is having a default a good idea?
    # TODO: Should we allow a list here so that multiple plugins are run?
    plugin = deposit_config.get("target", "invenio")

    try:
        ep, *eps = metadata.entry_points(group=group, name=plugin)
        if eps:
            # Entry point names in these groups refer to the deposition platforms. For
            # each platform, only a single implementation should exist. Otherwise we
            # would not be able to decide which implementation to choose.
            _log.error(
                f"Entry point name {plugin!r} is not unique within group {group!r}"
            )
            click_ctx.exit(1)
    except ValueError:  # not enough values to unpack
        _log.error(f"Entry point name {plugin!r} not found in group {group!r}")
        click_ctx.exit(1)

    # TODO: Could this raise an exception?
    deposit_plugin_class: BaseDepositPlugin = ep.load()
    deposit_plugin = deposit_plugin_class(click_ctx, ctx)

    try:
        deposit_plugin()
    except (RuntimeError, MisconfigurationError) as e:
        _log.error(f"Error in {group!r} entry point {plugin!r}: {e}")
        click_ctx.exit(1)


@click.group(invoke_without_command=True)
@click.pass_context
def postprocess(click_ctx: click.Context):
    """
    Postprocess metadata after deposition
    """
    _log = logging.getLogger('cli.postprocess')

    audit_log = logging.getLogger('audit')
    audit_log.info("# Post-processing")

    ctx = CodeMetaContext()

    if not (ctx.hermes_dir / "deposit").exists():
        _log.error("You must run the deposit command before post-process")
        click_ctx.exit(1)

    # Get all postprocessors
    postprocess_config = config.get("postprocess")
    postprocess_names = postprocess_config.get('execute', [])

    for postprocess_name in postprocess_names:
        postprocessors = metadata.entry_points(group='hermes.postprocess', name=postprocess_name)
        if not postprocessors:
            _log.warning("- Post-processor %s selected but not found.", postprocess_name)
            continue

        postprocessor_ep, *_ = postprocessors
        audit_log.info("## Post-process data with %s", postprocessor_ep.name)
        postprocessor = postprocessor_ep.load()
        postprocessor(ctx)

    audit_log.info('')
    logging.shutdown()


@click.group(invoke_without_command=True)
def clean():
    """
    Remove cached data.
    """
    audit_log = logging.getLogger('audit')
    audit_log.info("# Cleanup")

    # Create Hermes context (i.e., all collected metadata for all stages...)
    ctx = HermesContext()
    ctx.purge_caches()
