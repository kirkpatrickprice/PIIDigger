from piidigger.filehandlers import docx, pdf, plaintext, xls, xlsx

# Extension → handler and MIME → handler registries built from each module's HANDLES dict.
_EXT_REGISTRY: dict = {}
_MIME_REGISTRY: dict = {}

for _mod in (plaintext, pdf, docx, xlsx, xls):
    for _ext in _mod.HANDLES["ext"]:
        _EXT_REGISTRY[_ext] = _mod.handler
    for _mime in _mod.HANDLES["mime"]:
        _MIME_REGISTRY[_mime] = _mod.handler


def get_handler_for(ext: str, mime: str | None):
    """Return the FileHandler for a given extension and/or MIME type.

    MIME type is checked first (more specific); extension is the fallback.
    Returns None if no handler is registered for either.
    """
    if mime and mime in _MIME_REGISTRY:
        return _MIME_REGISTRY[mime]
    return _EXT_REGISTRY.get(ext)


def get_supported_exts() -> list[str]:
    """Return all file extensions with a registered handler."""
    return list(_EXT_REGISTRY.keys())


def get_supported_mimes() -> list[str]:
    """Return all MIME types with a registered handler."""
    return list(_MIME_REGISTRY.keys())
