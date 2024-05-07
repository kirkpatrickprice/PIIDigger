# PIIDIgger

**PIIDigger** is a program to identify Personally Identifiable Information in common file types

## Features
- Works anywhere Python is available
- Pre-built binaries available
- Customizable configuration file
- Identifies files based on file extension and MIME type
- Aware of OneDrive and Dropbox "cloud-only files" (see [ERRATA](https://github.com/kirkpatrickprice/PIIDigger/blob/main/ERRATA.md))
- Tunable [PERFORMANCE](https://github.com/kirkpatrickprice/PIIDigger/blob/main/PERFORMANCE.md) - especially useful for production servers
- Extensible file handlers to read any type of file
    - Initial release supports plain text files, Word Documents and Excel spreadsheets
    - See `--list-filetypes` command line option for currently supported file types
- Extensible data handlers to identify any type of data
    - Initial release supports primary account numbers for credit card data
    - See `--list-datahandlers` command line option for for currently supported document types
- Saves output in multiple formats
    - Initial releaase provides JSON and text file outputs

## Errata
Check out the [ERRATA](https://github.com/kirkpatrickprice/PIIDigger/blob/main/ERRATA.md) page for known issues, troubleshooting tips and instructions on reporting new problems.

## Performance Tuning
Check out the [PERFORMANCE](https://github.com/kirkpatrickprice/PIIDigger/blob/main/PERFORMANCE.md) page for notes on tuning performance, especially on production servers.

## Installation

### Binary Packages
You can download OS-specific binaries from the [releases](https://github.com/kirkpatrickprice/PIIDigger/releases) page.

Additional information on [Windows Releases](https://github.com/kirkpatrickprice/PIIDigger/blob/main/WINDOWS_RELEASES.md)

### Installing from Pip (e.g. MacOS and/or Linux)

NOTE: A virtual environment is strongly recommended to isolate PIIDigger and its dependencies from any other Python programs already on your system.  However, if you're not actively using Python, a system-wide installation is possible by running only the last command below.

**Linux/MacOS**

    python3 -m venv piidigger  #(or use your own folder name instead of "piidigger")
    source piidigger/bin/activate
    python3 -m pip install -U piidigger

PIIDigger will now be available as a program.  Run it with `piddigger` on the terminal prompt.

**Windows PowerShell**

    python.exe -m venv .venv  #(or use your own folder name instead of ".venv")
    .venv/Scripts/activate
    python.exe -m pip install -U piidigger[win]

PIIDigger will now be available as a program.  Run it with `piddigger.exe` in your PowerShell prompt.

NOTE:
* Update 26-MAR 2024: I'm trying a new packaging method that should avoid virus warnings from Defender and others.
* See the [ERRATA](https://github.com/kirkpatrickprice/PIIDigger/blob/main/ERRATA.md) page for information about antivirus products and packaged Python binaries.

## Usage
```
usage: piidigger [-h] [-c CREATECONFIGFILE] [-d] [-f CONFIGFILE] [-p MAXPROC] [--cpu-count] [--list-datahandlers]
                      [--list-filetypes]

Search the file system for Personally Identifiable Information

NOTES:
    * All program configuration is kept in 'piidigger.toml' -- a TOML-formatted configuration file
    * A default configuration will be used if the default 'piidigger.toml' file doesn't exist

options:
  -h, --help            show this help message and exit

Configuration:
  -c CREATECONFIGFILE, --create-conf CREATECONFIGFILE
                        Create a default configuration file for editing/reuse.
  -d, --default-conf    Use the default, internal config.
  -f CONFIGFILE, --conf-file CONFIGFILE
                        path/to/configfile.toml configuration file (Default = "piidigger.toml"). If the file is not
                        found, the default, internal configuration will be used.
  -p MAXPROC, --max-process MAXPROC
                        Override the number processes to use for searching files. Will use the lesser of CPU cores or
                        this value. On production servers, consider setting this to less than the number of physical
                        CPUs. See '--cpu-count' below.

Misc. Info:
  --cpu-count           Show the number of logical CPUs provided by the OS. Use this to tune performance. See '--max-
                        process' above.
  --list-datahandlers   Display the list of data handlers and exit
  --list-filetypes      Display the list of file types and exit
```

If a configuration file doesn't exist, PIIDigger will use a default configuration as shown below.

## Advanced Configurations

All other options are configured from the configuration file.  In most cases, the defaults should work just fine.  You can create a configuration file with the `-c piidigger.toml` option.  `piidigger.toml` is the default file and if found, PIIDigger will use it automatically.  You can also create as many different configuration files as you like and reference them with `piidigger -f <filename>`.

An explanation of the configuration file options follows:


```
dataHandlers = ["pan"]

localFilesOnly = true

[results]
path = "piidigger-results/"
json = true
text = true

[includeFiles]
ext = "all"
mime = "all"

[includeFiles.startDirs]
windows = "all"
linux = ["/"]
darwin = ["/"]

[excludeDirs]
windows = ["C:\\Windows", "C:\\Program Files (x86)", "C:\\Program Files"]
linux = ["/proc", "/sys", "/dev", "/usr/bin", "/usr/lib", "/usr/lib32", "/usr/lib64", "/usr/libx32", "/usr/sbin", "*/.vscode-server", "/mnt/c", "/mnt/d", "/mnt/wslg"]
darwin = ["/dev", "/usr/bin", "/usr/lib", "/usr/sbin", "/Applications", "/System"]

[logging]
logLevel = "INFO"
logFile = "logs/piidigger.log"
```

| Option                                | Description  |
| ------                                | ----------   |
| `dataHandlers`                        | Default = `"pan"`.  Provides a list of the datahandlers that should be used.  "All" will load all data handlers currently defined in the datahandlers module.  To limit the selection, use a `[bracket-list]`, such as `['pan', 'ssn']`. |
| `localFilesOnly`                      | Default True.  For OneDrive and Dropbox files on Windows, only scan files which are already on the local disk.
| `[results]path`                       | Where to save the results to.  Current output formats are JSON and text files.  A folder name can be included and PIIDigger will create any missing folders in the path. |
| `[results]json`                       | Default True.  Whether to create a JSON output file |
| `[results]csv`                        | Default True.  Whether to create a CSV output file |
| `[includeFiles]`                      | Defines the criteria by which files will be included in the scan |
| `[includeFiles]ext`                   | Default = `"all"`.  The file extensions to include.  "All" will collect all supported file extensions from the file handlers currently supported.  To limit the selection, use a `[bracket-list]`, such as `['.txt', '.xlsx']`. |
| `[includeFiles]mime`                  | Default = `"all"`.  The file extensions to include.  "All" will collect all supported file extensions from the file handlers currently supported.  To limit the selection, use a `[bracket-list]`, such as `['text/plain-text', 'application/vnd.ms-excel']`. |
| `[includeFiles.startDirs]`            | For each operating system, define the starting directories/drives to start the search from.  OS types are `windows`, `linux`, `darwin` (for MacOS).  For each OS type, you can also use a `[bracket-list]` to provide specific starting points, such as `['C:\Users\<username>']` on Windows or `['/home/<username>']` on Linux/MacOS. |
| `...[startDirs\]windows`              | Default = `"all"` which will identify all currently accessible drive letters on the system.  NOTE: This also includes network drives, which might not be desired behavior.  You can use the `excludeDirs` option below to remove any network-mapped drive letters from the scan. |
| `...[startDirs\]linux` and `darwin`   | Default = `["/"]`, or scan the entire file system.  If there are network-mounted paths, you can exclude those with the `excludeDirs` option below.
| `[excludeDirs]`                       | For each operating system, a `[bracket-list]` of the folders/directories to exclude.  The defaults exclude operating system-specific directories such as `C:\Windows` and `/usr/bin`.  Additional patterns can be supplied and will match as a simple string (no wildcards, regex or glob patterns) from the beginning of the path.  `[results]` and `[logFile]` folders will always be excluded  |
| `[logging]`                           | Define the logging level and log file destination.  The defaults should always be fine, unless directed to create a DEBUG-level log file for troubleshooting |
| `[logging]logLevel`                   | Default = `"INFO"`, can be overridden using  Python logging levels (https://docs.python.org/3/howto/logging.html).  Must be in ALL CAPS and enclosed in quotes.  Would normally be either "INFO" (default) or, if advised for troubleshooting purposes, "DEBUG" |
| `[logging]logFile`                    | Default = "logs/piidigger.log" which should be just fine. |
