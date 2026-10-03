from piidigger.filehandlers import docx, pdf, plaintext, xls, xlsx

# Extension → handler and MIME → handler registries built from each module's HANDLES dict.
_EXT_REGISTRY: dict = {}
_MIME_REGISTRY: dict = {}

for _mod in (plaintext, pdf, docx, xlsx, xls):
    for _ext in _mod.HANDLES["ext"]:
        _EXT_REGISTRY[_ext.lower()] = _mod.handler
    for _mime in _mod.HANDLES["mime"]:
        _MIME_REGISTRY[_mime] = _mod.handler


def get_handler_for(ext: str, mime: str | None):
    """Return the FileHandler for a given extension and/or MIME type.

    MIME type is checked first (more specific); extension is the fallback.
    Extensions match regardless of case, so REPORT.PDF finds the PDF handler.
    Returns None if no handler is registered for either.
    """
    if mime and mime in _MIME_REGISTRY:
        return _MIME_REGISTRY[mime]
    return _EXT_REGISTRY.get(ext.lower())


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

    When a file is scanned, the handler is chosen as get_handler_for() chooses
    it: by MIME type first, when that is known, then by extension.
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
