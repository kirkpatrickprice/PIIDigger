from __future__ import annotations

import os
from pathlib import Path

import click
import psutil

from piidigger.archivehandlers import HANDLER_REGISTRY as ARCHIVE_HANDLER_REGISTRY
from piidigger.archivehandlers import detect_archive_type
from piidigger.datahandlers import HANDLER_REGISTRY
from piidigger.filehandlers import file_ext, get_handler_for, get_supported_exts, get_supported_mimes, select_handler
from piidigger.filehandlers.plaintext import handler as plaintext_handler
from piidigger.getencoding import detect_file_encoding
from piidigger.getmime import get_mime
from piidigger.models.config import Config
from piidigger.orchestration.worker import configured_archive_type


@click.group(name="inspect")
def inspect_group() -> None:
    """Inspect PIIDigger runtime capabilities and file metadata."""


def _load_config(config_path: Path | None) -> tuple[Config, str]:
    """Load the config a scan would use: -f, else piidigger.toml in the current directory, else the defaults."""
    path = config_path or Path("piidigger.toml")
    if config_path is None and not path.exists():
        return Config.default(), "built-in defaults"
    try:
        return Config.from_toml(path), str(path)
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc


def _not_scanned_reason(name: str, ext: str, mime: str | None, config: Config) -> str:
    """Say which setting or part of select_handler()'s rule turned the file away."""
    archive_type = detect_archive_type(name)
    if archive_type is not None:
        if not config.archives.enabled:
            return "archives.enabled is false"
        return f"archives.formats does not list {archive_type}"
    if ext.lower() in get_supported_exts():
        return f"include_exts does not list {ext.lower()}"
    if not mime:
        return "its extension is not recognized and no MIME type was detected"
    if mime not in get_supported_mimes():
        return f"its extension is not recognized and no file handler reads {mime}"
    return f"its extension is not recognized and include_mime does not list {mime}"


@inspect_group.command(name="file")
@click.argument("file_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "-f",
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to TOML configuration file.  Defaults to 'piidigger.toml' in the current directory if it exists.",
)
def inspect_file(file_path: Path, config_path: Path | None) -> None:
    """Show how a scan would handle FILE_PATH: its extension, MIME type, encoding and outcome."""
    config, config_name = _load_config(config_path)
    ext = file_ext(file_path.name)

    # The listing skips these before it looks at anything else.  Sniffing a FIFO would also never return.
    skipped = "symbolic links are skipped" if file_path.is_symlink() else None
    if skipped is None and not file_path.is_file():
        skipped = "it is not a regular file"
    if skipped is not None:
        click.echo(f"Extension: {ext.lower() or '(none)'}")
        click.echo(f"Config:    {config_name}")
        click.echo(f"Outcome:   Not scanned: {skipped}")
        return

    try:
        mime = get_mime(str(file_path))
        handler = select_handler(ext, mime, config.include_exts, config.include_mime)
        archive_type = configured_archive_type(file_path.name, config)
        reads_as_text = archive_type is None and handler is plaintext_handler
        encoding = detect_file_encoding(file_path) if reads_as_text else None
        is_empty = file_path.stat().st_size == 0
    except OSError as exc:
        raise click.ClickException(str(exc)) from exc

    if archive_type is not None:
        outcome = f"Scanned: members are read from this {archive_type} archive"
    elif handler is None:
        outcome = f"Not scanned: {_not_scanned_reason(file_path.name, ext, mime, config)}"
    elif reads_as_text and not encoding and not is_empty:
        outcome = "Not read: text encoding could not be detected"
    else:
        outcome = f"Scanned with {type(handler).__name__}"

    click.echo(f"Extension: {ext.lower() or '(none)'}")
    click.echo(f"MIME type: {mime or '(not detected)'}")
    click.echo(f"Encoding:  {encoding or '(not detected)'}" if reads_as_text else "Encoding:  (not used)")
    click.echo(f"Config:    {config_name}")
    click.echo(f"Outcome:   {outcome}")
    if reads_as_text and mime in get_supported_mimes() and get_handler_for("", mime) is not plaintext_handler:
        click.echo(f"Note:      content looks like {mime}, but a text extension is always read as text")


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
