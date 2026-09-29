from __future__ import annotations

from importlib.metadata import version as _pkg_version

import click

from piidigger.cli.commands.config import config_group
from piidigger.cli.commands.inspect import inspect_group
from piidigger.cli.commands.scan import scan


@click.group(
    context_settings={"help_option_names": ["-h", "--help"]},
    invoke_without_command=True,
)
@click.version_option(
    _pkg_version("piidigger"),
    "-v",
    "--version",
    prog_name="PIIDigger",
    message="PIIDigger version: %(version)s",
)
@click.pass_context
def cli(ctx: click.Context) -> None:
    """PIIDigger — scan for Personally Identifiable Information."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(scan)


cli.add_command(scan)
cli.add_command(config_group, name="config")
cli.add_command(inspect_group, name="inspect")
