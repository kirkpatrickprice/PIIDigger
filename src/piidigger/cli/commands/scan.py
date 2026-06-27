from __future__ import annotations

import os
import sys
from importlib.metadata import version as _pkg_version
from pathlib import Path

import click

from piidigger.datahandlers import HANDLER_REGISTRY
from piidigger.filehandlers import get_supported_exts, get_supported_mimes
from piidigger.models.config import Config
from piidigger.run import run_scan


@click.command(name="scan")
@click.option(
    "-c", "--config", "config_file",
    default="piidigger.toml",
    type=click.Path(),
    show_default=True,
    help="Path to TOML configuration file.  Falls back to built-in defaults if not found.",
)
@click.option(
    "-d", "--default-config", "use_default",
    is_flag=True,
    help="Ignore any config file and use built-in defaults.",
)
@click.option(
    "-w", "--max-workers", "max_workers",
    default=0,
    type=int,
    help="Override the number of worker processes (0 = use config / cpu_count).",
)
@click.option(
    "--cpu-count",
    is_flag=True,
    help="Print the number of logical CPUs and exit.",
)
@click.option(
    "--list-datahandlers",
    is_flag=True,
    help="List available data handlers and exit.",
)
@click.option(
    "--list-filetypes",
    is_flag=True,
    help="List supported file extensions and MIME types and exit.",
)
@click.version_option(
    _pkg_version("piidigger"),
    "-v", "--version",
    prog_name="PIIDigger",
    message="PIIDigger version: %(version)s",
)
def scan(
    config_file: str,
    use_default: bool,
    max_workers: int,
    cpu_count: bool,
    list_datahandlers: bool,
    list_filetypes: bool,
) -> None:
    """Scan directories for PII."""
    if cpu_count:
        click.echo(f"Logical CPUs: {os.cpu_count()}")
        sys.exit(0)

    if list_datahandlers:
        click.echo("Available data handlers:")
        for name in sorted(HANDLER_REGISTRY):
            click.echo(f"  {name}")
        sys.exit(0)

    if list_filetypes:
        click.echo("Supported extensions:")
        for ext in sorted(get_supported_exts()):
            click.echo(f"  {ext}")
        click.echo("Supported MIME types:")
        for mime in sorted(get_supported_mimes()):
            click.echo(f"  {mime}")
        sys.exit(0)

    if use_default:
        config = Config.default()
    else:
        p = Path(config_file)
        if p.exists():
            try:
                config = Config.from_toml(p)
            except ValueError as exc:
                click.echo(f"Error: {exc}", err=True)
                sys.exit(1)
        else:
            click.echo(f"Config file {config_file!r} not found — using built-in defaults.", err=True)
            config = Config.default()

    if max_workers > 0:
        config = config.model_copy(update={"max_workers": max_workers})

    sys.exit(run_scan(config))
