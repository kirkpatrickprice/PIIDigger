from pathlib import PurePosixPath

from piidigger.filehandlers import docx, pdf, plaintext, xls, xlsx

# Extension → handler and MIME → handler registries built from each module's HANDLES dict.
_EXT_REGISTRY: dict = {}
_MIME_REGISTRY: dict = {}

for _mod in (plaintext, pdf, docx, xlsx, xls):
    for _ext in _mod.HANDLES["ext"]:
        _EXT_REGISTRY[_ext.lower()] = _mod.handler
    for _mime in _mod.HANDLES["mime"]:
        _MIME_REGISTRY[_mime] = _mod.handler


def file_ext(name: str) -> str:
    """The extension handlers are chosen by: a dotfile's whole name, otherwise its suffix.

    Path(".env").suffix is "", so a file named exactly .env would otherwise never
    match the ".env" extension.  A backslash separates folders here on every
    platform: an archive made on Windows can name "a.dist-info\\licenses\\src",
    whose suffix would otherwise be most of that path.
    """
    path = PurePosixPath(name.replace("\\", "/"))
    return path.suffix or (path.name if path.name.startswith(".") else "")


def get_handler_for(ext: str, mime: str | None):
    """Return the FileHandler for a given extension and/or MIME type.

    A plain-text extension always gets the text handler.  Content sniffing calls
    a file a PDF when %PDF appears near its start, so a CSV or source file
    holding that text would otherwise go to the PDF handler and go unread.

    Any other file goes by its MIME type first, then its extension.  A document's
    extension is less reliable than its content: applications export HTML as .xls,
    and a workbook named .docx would otherwise read as an empty Word document.

    Extensions match regardless of case, so REPORT.PDF finds the PDF handler.
    Returns None if no handler is registered for either.
    """
    handler = _EXT_REGISTRY.get(ext.lower())
    if handler is plaintext.handler or not mime:
        return handler
    return _MIME_REGISTRY.get(mime, handler)


def mime_can_change_handler(ext: str) -> bool:
    """Whether a file's MIME type could affect how it is scanned.  False for a plain-text extension.

    Sniffing one JavaScript file can outlast a directory listing's whole deadline,
    so the listing skips it when the answer can't matter.
    """
    return _EXT_REGISTRY.get(ext.lower()) is not plaintext.handler


def select_handler(ext: str, mime: str | None, include_exts: list[str], include_mime: list[str]):
    """Decide whether a file is scanned, and return its FileHandler if so (None if not).

    Used for files on disk and for archive members alike, so both follow one
    rule.  The extension decides; MIME is the fallback:

    * A recognised extension (case-insensitive) is scanned when include_exts
      lists it or is ["all"].  include_mime plays no part.
    * A file whose extension is not recognised (none, or unknown) is scanned
      when its detected MIME type has a handler and include_mime lists it or is
      ["all"].  Archive members have no detected MIME type, so an archive
      member with an unrecognised extension is never scanned.

    When a file is scanned, get_handler_for() chooses its handler.
    """
    ext = ext.lower()
    if ext in _EXT_REGISTRY:
        if "all" in include_exts or ext in {e.lower() for e in include_exts}:
            return get_handler_for(ext, mime)
        return None
    if mime and mime in _MIME_REGISTRY and ("all" in include_mime or mime in include_mime):
        return _MIME_REGISTRY[mime]
    return None


def get_supported_exts() -> list[str]:
    """Return all file extensions with a registered handler."""
    return list(_EXT_REGISTRY.keys())


def get_supported_mimes() -> list[str]:
    """Return all MIME types with a registered handler."""
    return list(_MIME_REGISTRY.keys())
