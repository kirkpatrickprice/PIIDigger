import ctypes
import os
import platform

from piidigger import console
from piidigger import datahandlers as dh
from piidigger import filehandlers as fh
from piidigger import outputhandlers as oh
from piidigger.globalvars import MAX_CHUNK_SIZE

# Dynamically build the supported file handlers based on the contents of the filehandlers package.
# Each file handler needs a globally-defined variable called "handles" with a dictionary as follows:
#   ext: [a list of file extensions with the leading .]
#   mime: [a list of mime-type strings]

FILE_HANDLERS = {
    handler: getattr(fh, handler).handles
    for handler in fh.__dir__()
    if not handler.startswith("_")
}


######################################################
############### Global functions #####################
######################################################


def count_results(results: dict) -> int:
    """Receives a dictionary of results and returns the total match count across all match sets in the dictionary"""
    count = 0
    for key in results:
        item = results[key]
        if isinstance(item, dict):
            count += count_results(item)
        elif isinstance(item, list):
            count += len(item)

    return count


def get_all_data_handler_modules() -> list:
    """
    Returns a list containing the data handler modules from datahandler package
    """
    return [getattr(dh, module) for module in get_supported_data_handler_names()]


def get_data_handler_module(name: str):
    """
    Returns a module object for a specific data handler
    """
    try:
        return getattr(dh, name)
    except Exception:
        return None


def get_default_config() -> dict:
    """
    Returns a default configuration object for when a configuration file couldn't be found.
    """
    return {
        "dataHandlers": [
            "pan",
            "email",
        ],
        "localFilesOnly": True,
        "results": {
            "path": "piidigger-results/",
            "csv": True,
            "json": True,
            "text": True,
        },
        "includeFiles": {
            "ext": "all",
            "mime": "all",
            "startDirs": {"windows": "all", "linux": ["/"], "darwin": ["/"]},
        },
        "excludeDirs": {
            "windows": ["C:\\Windows", "C:\\Program Files (x86)", "C:\\Program Files"],
            "linux": [
                "/boot",
                "/dev",
                "/etc",
                "/proc",
                "/run",
                "/snap",
                "/sys",
                "/usr/bin",
                "/usr/lib",
                "/usr/lib32",
                "/usr/lib64",
                "/usr/libx32",
                "/usr/local",
                "/usr/sbin",
                "/usr/share",
                "/usr/src/",
                "*/.vscode-server",
                "/mnt/c",
                "/mnt/d",
                "/mnt/wslg",
                "/wsl",
            ],
            "darwin": [
                "/dev",
                "/etc",
                "/usr/bin",
                "/usr/local/Homebrew",
                "/usr/lib",
                "/usr/sbin",
                "/Applications",
                "/Library/Developer",
                "/Library/Documentation",
                "/System",
            ],
        },
        "dataHandlerTimeouts": {
            "pan": 0,
            "email": 30,
        },
        "logging": {"logLevel": "INFO", "logFile": "logs/piidigger.log"},
    }


def get_enabled_data_handler_modules(module_names: list):
    """
    Receives a list of enabled module names and returns a list of the module objects
    """
    return [get_data_handler_module(name) for name in module_names]


def get_file_handler_name(ext: str, mime: str) -> str | None:
    """
    Receives a file extension and MIME type

    Returns a file handler to use to work with the file or None

    All file handlers should be in the "handler" module/directory in a Python file by the name of handler
    """
    handler = None

    for key in FILE_HANDLERS:
        if (ext in FILE_HANDLERS[key]["ext"]) or (mime in FILE_HANDLERS[key]["mime"]):
            handler = str(key)
            break

    return handler


def get_file_handler_module(name):
    """
    Returns a module object for a file handler by the provided name
    """
    try:
        return getattr(fh, name)
    except Exception:
        return None


def get_output_handler_module(name: str):
    """
    Returns a module object for a specific output handler
    """
    try:
        return getattr(oh, name)
    except Exception:
        return None


def get_os_type() -> str:
    return platform.system().lower()


def get_supported_data_handler_names() -> list:
    return [n for n in dh.__dir__() if not n.startswith("__")]


def get_supported_file_exts() -> list:
    """
    Returns a list of all supported file extensions
    """
    exts = []

    for key in FILE_HANDLERS:
        exts += FILE_HANDLERS[key]["ext"]

    return exts


def get_supported_file_mimes() -> list:
    """
    Returns a list of all supported MIME types
    """
    mimes = []

    for key in FILE_HANDLERS:
        mimes += FILE_HANDLERS[key]["mime"]

    return mimes


def is_admin() -> bool:
    try:
        check = os.geteuid() == 0
    except AttributeError:
        check = ctypes.windll.shell32.IsUserAnAdmin() != 0
    return check


def make_chunks(s: str, chunk_size: int = MAX_CHUNK_SIZE) -> list:
    """Breaks up a string into smaller strings not larger than chunk_size"""
    words = s.split()
    word_num = 0
    chunks = list()

    while word_num < len(words):
        chunk = ""
        while len(chunk) < chunk_size and word_num < len(words):
            if len(words[word_num]) > chunk_size:
                # Break up super-long strings into shorter words and add them to the word list
                chunk_list = [
                    words[word_num][i : i + chunk_size]
                    for i in range(0, len(words[word_num]), chunk_size)
                ]
                words = [*words, *chunk_list]
            else:
                # Add the next word to the current chunk
                chunk += words[word_num] + " "
            word_num += 1

        # Add the current chunk to the list of chunks
        chunks += [chunk.strip()]

    return chunks


def process_matches(results: dict, matches: dict, dh_name: str) -> dict:
    """Process the results from RE matches and add them to the results dictionary"""
    for key in matches:
        value = matches[key]
        if value:
            if dh_name not in results["matches"]:
                results["matches"][dh_name] = dict()
            if key not in results["matches"][dh_name]:
                results["matches"][dh_name][key] = set()
            results["matches"][dh_name][key].update(value)

    return results


def progress_line(*pargs, **kwargs):
    """
    Prints a status line that includes details about directories, files, and results
    """
    screen_width = console.get_terminal_size()[0]

    line = (
        f"Folders scanned: {kwargs['totalDirs'].value} | "
        f"Files identified: {kwargs['totalFilesFound'].value} | "
        f"Files scanned: {kwargs['totalFilesScanned'].value} | "
        f"Results found: {kwargs['totalResults'].value}"
    )

    if len(line) > screen_width:
        line = line[: screen_width - 1]

    console.status(line)


def sizeof_fmt(num, suffix="B"):
    """
    Returns a human-readable string for bytes.
    """
    # Taken from https://stackoverflow.com/questions/1094841/get-human-readable-version-of-file-size
    for unit in ("", "K", "M", "G", "T", "P", "E", "Z"):
        if abs(num) < 1024.0:
            return f"{num:.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}Y{suffix}"


def write_default_config(toml_file: str):
    # This is a total kluge, but without a reasonable Python library to write a TOML v1.1 file based on a Python dictionary, we have to build the default config file from scratch

    def _tomlfy(key, value):
        if isinstance(value, str):
            return key + ' = "' + value + '"'
        if isinstance(value, bool):
            return key + " = " + str(value).lower()
        return key + " = " + str(value).replace("'", '"')

    default_config = get_default_config()
    lines = list()
    for key in ["dataHandlers"]:
        lines.append(_tomlfy(key, default_config[key]))

    lines.append("")
    for key in ["localFilesOnly"]:
        lines.append(_tomlfy(key, default_config[key]))

    lines.append("")
    lines.append("[results]")
    for key in default_config["results"].keys():
        lines.append(_tomlfy(key, default_config["results"][key]))

    lines.append("")
    lines.append("[includeFiles]")
    for key in ["ext", "mime"]:
        lines.append(_tomlfy(key, default_config["includeFiles"][key]))

    lines.append("")
    lines.append("[includeFiles.startDirs]")
    for key in ["windows", "linux", "darwin"]:
        lines.append(_tomlfy(key, default_config["includeFiles"]["startDirs"][key]))

    lines.append("")
    lines.append("[excludeDirs]")
    for key in ["windows", "linux", "darwin"]:
        lines.append(_tomlfy(key, default_config["excludeDirs"][key]))

    lines.append("")
    lines.append("[logging]")
    for key in ["logLevel", "logFile"]:
        lines.append(_tomlfy(key, default_config["logging"][key]))

    try:
        with open(toml_file, "w") as tf:
            tf.writelines(line + "\n" for line in lines)
        return "Success"
    except Exception as e:
        return str(e)
