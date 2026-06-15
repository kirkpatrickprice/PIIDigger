import multiprocessing as mp
import sys
from os import cpu_count

import click

import piidigger.classes as classes
from piidigger import __version__, console, globalfuncs
from piidigger.globalvars import ERROR_CODES
from piidigger.run import run_scan


@click.command(
    context_settings={'help_option_names': ['-h', '--help']},
    epilog=(
        'NOTES:\n\n'
        '  * All program configuration is kept in "piidigger.toml" -- a TOML-formatted configuration file.\n\n'
        '  * A default configuration will be used if the default "piidigger.toml" file does not exist.'
    ),
)
@click.option(
    '-c', '--create-conf', 'createConfigFile', default='', metavar='FILE',
    help='Create a default configuration file for editing/reuse.',
)
@click.option(
    '-d', '--default-conf', 'defaultConfig', is_flag=True,
    help='Use the default, internal config.',
)
@click.option(
    '-f', '--conf-file', 'configFile', default='piidigger.toml',
    type=click.Path(), show_default=True,
    help='path/to/configfile.toml configuration file.  If the file is not found, the default, internal configuration will be used.',
)
@click.option(
    '-p', '--max-process', 'maxProc', default=0, type=int,
    help='Override the number of processes to use for searching files.  Will use the lesser of CPU cores or this value.  On production servers, consider setting this to less than the number of physical CPUs.  See "--cpu-count" below.',
)
@click.option(
    '--cpu-count', 'cpuCount', is_flag=True,
    help='Show the number of logical CPUs provided by the OS.  Use this to tune performance.  See "--max-process" above.',
)
@click.option(
    '--list-datahandlers', 'listDH', is_flag=True,
    help='Display the list of data handlers and exit.',
)
@click.option(
    '--list-filetypes', 'listFT', is_flag=True,
    help='Display the list of file types and exit.',
)
@click.version_option(
    __version__, '-v', '--version',
    prog_name='PIIDigger', message='PIIDigger version: %(version)s',
)
def main(createConfigFile, defaultConfig, configFile, maxProc, cpuCount, listDH, listFT):
    '''Search the file system for Personally Identifiable Information.'''
    if cpuCount:
        click.echo(f'CPU cores: {cpu_count()}')
        sys.exit(ERROR_CODES['ok'])

    if listDH:
        click.echo(f'Data handler modules:  {globalfuncs.get_supported_data_handler_names()}')
        sys.exit(ERROR_CODES['ok'])

    if listFT:
        click.echo(f'File extns:  {globalfuncs.get_supported_file_exts()}')
        click.echo(f'MIME types:  {globalfuncs.get_supported_file_mimes()}')
        sys.exit(ERROR_CODES['ok'])

    if len(createConfigFile) > 0:
        tomlFile = str(createConfigFile) if str(createConfigFile).endswith('.toml') else str(createConfigFile) + '.toml'
        configFileWritten = globalfuncs.write_default_config(tomlFile)

        if configFileWritten == 'Success':
            console.normal('Default configuration written to ' + createConfigFile)
            sys.exit(ERROR_CODES['ok'])
        else:
            console.error(f'Config file not written: {configFileWritten}')
            sys.exit(ERROR_CODES['unknownError'])

    if defaultConfig:
        config = classes.Config(configFile='', useDefault=True)
    else:
        config = classes.Config(configFile=configFile)

    if maxProc > 0:
        config.setMaxProcs(min(cpu_count() or 1, maxProc))

    sys.exit(run_scan(config))

if __name__ == '__main__':
    mp.freeze_support()
    main()
