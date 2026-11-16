from __future__ import annotations

import sys
from pathlib import Path

import click

from piidigger.models.config import Config, generate_toml_template
from piidigger.orchestration.escaping import terminal_text


@click.group(name="config")
def config_group() -> None:
    """Manage PIIDigger configuration files."""


@config_group.command(name="generate")
@click.argument("output", default="piidigger.toml", metavar="FILE")
def generate(output: str) -> None:
    """Write a default configuration file to FILE (default: piidigger.toml)."""
    dest = Path(output)
    if dest.exists():
        click.echo(f"Error: {output!r} already exists.  Delete it or choose a different path.", err=True)
        sys.exit(1)
    try:
        dest.write_text(generate_toml_template(), encoding="utf-8")
        click.echo(f"Default config written to {terminal_text(output)}")
    except OSError as exc:
        click.echo(f"Error writing {output!r}: {terminal_text(str(exc))}", err=True)
        sys.exit(1)


@config_group.command(name="validate")
@click.argument("config_file", default="piidigger.toml", metavar="FILE")
def validate(config_file: str) -> None:
    """Validate a configuration file and report any errors."""
    try:
        Config.from_toml(Path(config_file))
        click.echo(f"{config_file!r}: OK")
    except ValueError as exc:
        click.echo(f"Error: {terminal_text(str(exc))}", err=True)
        sys.exit(1)
