from __future__ import annotations

import os
from pathlib import Path

import click
import psutil

from piidigger.archivehandlers import HANDLER_REGISTRY as ARCHIVE_HANDLER_REGISTRY
from piidigger.datahandlers import HANDLER_REGISTRY
from piidigger.filehandlers import get_supported_exts, get_supported_mimes
from piidigger.getencoding import detect_encoding
from piidigger.getmime import get_mime


@click.group(name="inspect")
def inspect_group() -> None:
    """Inspect PIIDigger runtime capabilities and file metadata."""


@inspect_group.command(name="mime")
@click.argument("file_path", type=click.Path(exists=True, dir_okay=True, path_type=Path))
def inspect_mime(file_path: Path) -> None:
    """Print the MIME type PIIDigger would assign to FILE_PATH."""
    click.echo(get_mime(str(file_path)))


@inspect_group.command(name="encoding")
@click.argument("file_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def inspect_encoding(file_path: Path) -> None:
    """Print the encoding PIIDigger would use to read FILE_PATH."""
    try:
        click.echo(detect_encoding(file_path.read_bytes()))
    except OSError as exc:
        raise click.ClickException(str(exc)) from exc


@inspect_group.command(name="archivetypes")
def inspect_archivetypes() -> None:
    """List supported archive formats."""
    for name in sorted(ARCHIVE_HANDLER_REGISTRY):
        click.echo(name)


@inspect_group.command(name="datatypes")
def inspect_datatypes() -> None:
    """List available data handlers."""
    for name in sorted(HANDLER_REGISTRY):
        click.echo(name)


@inspect_group.command(name="filetypes")
def inspect_filetypes() -> None:
    """List supported file extensions and MIME types."""
    click.echo("Supported extensions:")
    for ext in sorted(get_supported_exts()):
        click.echo(f"  {ext}")
    click.echo("Supported MIME types:")
    for mime in sorted(get_supported_mimes()):
        click.echo(f"  {mime}")


@inspect_group.command(name="cpu")
def inspect_cpu() -> None:
    """Print the physical and logical CPU counts available to PIIDigger."""
    logical_cpus = os.cpu_count() or 1
    physical_cpus = psutil.cpu_count(logical=False) or logical_cpus
    click.echo(f"Physical CPUs: {physical_cpus}")
    click.echo(f"Logical CPUs: {logical_cpus}")
