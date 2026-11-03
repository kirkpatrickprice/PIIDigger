from __future__ import annotations

import click

from piidigger import __version__
from piidigger.cli.commands.config import config_group
from piidigger.cli.commands.inspect import inspect_group
from piidigger.cli.commands.scan import scan

# Builds run .github/scripts/write_build_info.py, which writes the git-ignored _build_info.py.
try:
    from piidigger._build_info import COMMIT  # type: ignore[import-not-found,unused-ignore]
except ImportError:
    COMMIT = None  # type: ignore[assignment,unused-ignore]


@click.group(
    context_settings={"help_option_names": ["-h", "--help"]},
    invoke_without_command=True,
)
@click.version_option(
    f"{__version__} ({COMMIT})" if COMMIT else __version__,
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
