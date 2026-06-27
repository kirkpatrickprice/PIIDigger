from __future__ import annotations

import sys
from pathlib import Path

import click

from piidigger.models.config import Config
from piidigger.run import run_scan


@click.command(name="scan")
@click.option(
    "-c",
    "--config",
    "config_file",
    default="piidigger.toml",
    type=click.Path(),
    show_default=True,
    help="Path to TOML configuration file.  Falls back to built-in defaults if not found.",
)
@click.option(
    "-d",
    "--default-config",
    "use_default",
    is_flag=True,
    help="Ignore any config file and use built-in defaults.",
)
@click.option(
    "-w",
    "--max-workers",
    "max_workers",
    default=None,
    type=int,
    hidden=True,
    help="Expert override for the number of worker processes.",
)
def scan(
    config_file: str,
    use_default: bool,
    max_workers: int | None,
) -> None:
    """Scan directories for PII."""
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

    if max_workers is not None:
        config = config.model_copy(update={"max_workers": max_workers})

    sys.exit(run_scan(config))
