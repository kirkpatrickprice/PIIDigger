from multiprocessing import freeze_support

from piidigger.cli.main import cli

if __name__ == "__main__":
    freeze_support()
    cli()
