# This looks like a better cross-platform implementation.  Will need to look at implementing as the current "magic" package fails on Windows
# https://github.com/cdgriffith/puremagic

import contextlib
import os
import sys

import click

with contextlib.suppress(ImportError):
    import puremagic

moduleName='getmime'

def testMagic() -> bool:
    return 'puremagic' in sys.modules

def getMime(filename: str) -> str:
    if not isinstance(filename, str):
        filename=str(filename)
    if os.path.isdir(filename):
        return "Directory"
    
    try:
        mimeType = puremagic.from_file(filename, mime=True) if testMagic() else None
    except Exception: 
        mimeType = None
        
    
    return mimeType

@click.command(context_settings={'help_option_names': ['-h', '--help']})
@click.argument('files', nargs=-1, type=click.Path())
def main(files):
    '''Report the detected MIME type for each given file.'''
    if not testMagic():
        click.echo('Mime detection library could not be loaded')
        return
    for arg in files:
        if os.path.exists(arg):
            click.echo(f'Filename: {arg}\nMime: {getMime(arg)}\n')
        else:
            click.echo(f'{arg}: File not found')

if __name__ == "__main__":
    main()