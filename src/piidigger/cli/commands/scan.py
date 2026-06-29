from __future__ import annotations

import sys
from pathlib import Path

import click

from piidigger.models.config import Config
from piidigger.run import run_scan


@click.command(name="scan")
@click.option(
    "-f",
    "--config",
    "config_file",
    default=None,
    type=click.Path(),
    help="Path to TOML configuration file.  Defaults to 'piidigger.toml' in the current directory if it exists.",
)
@click.option(
    "-d",
    "--default-config",
    "use_default",
    is_flag=True,
    help="Ignore any config file and use built-in defaults.",
)
def scan(
    config_file: str | None,
    use_default: bool,
) -> None:
    """Scan directories for PII."""
    if use_default:
        config = Config.default()
    elif config_file is not None:
        # User explicitly specified a file — treat a missing file as an error.
        p = Path(config_file)
        if not p.exists():
            click.echo(f"Error: config file {config_file!r} not found.", err=True)
            sys.exit(1)
        try:
            config = Config.from_toml(p)
        except ValueError as exc:
            click.echo(f"Error: {exc}", err=True)
            sys.exit(1)
    else:
        # No file specified — silently check for piidigger.toml in the current directory.
        default_path = Path("piidigger.toml")
        if default_path.exists():
            try:
                config = Config.from_toml(default_path)
            except ValueError as exc:
                click.echo(f"Error: {exc}", err=True)
                sys.exit(1)
        else:
            config = Config.default()

    sys.exit(run_scan(config))
