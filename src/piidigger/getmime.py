# This looks like a better cross-platform implementation.  Will need to look at implementing as the current "magic" package fails on Windows
# https://github.com/cdgriffith/puremagic

import contextlib
import os
import sys

import click

with contextlib.suppress(ImportError):
    import puremagic

module_name = 'getmime'


def test_magic() -> bool:
    return 'puremagic' in sys.modules


def get_mime(filename: str) -> str | None:
    if not isinstance(filename, str):
        filename = str(filename)
    if os.path.isdir(filename):
        return "Directory"

    try:
        mime_type = puremagic.from_file(filename, mime=True) if test_magic() else None
    except Exception:
        mime_type = None

    return mime_type


@click.command(context_settings={'help_option_names': ['-h', '--help']})
@click.argument('files', nargs=-1, type=click.Path())
def main(files):
    '''Report the detected MIME type for each given file.'''
    if not test_magic():
        click.echo('Mime detection library could not be loaded')
        return
    for arg in files:
        if os.path.exists(arg):
            click.echo(f'Filename: {arg}\nMime: {get_mime(arg)}\n')
        else:
            click.echo(f'{arg}: File not found')


if __name__ == "__main__":
    main()
