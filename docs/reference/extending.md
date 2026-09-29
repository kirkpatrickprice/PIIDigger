# Extending PIIDigger

PIIDigger is distributed and used as a CLI tool — nobody is expected to `import piidigger` as a dependency in their own project, so this page isn't a public library API reference. It's aimed at **contributors** (including future-you, months from now) who want to add a new file type, a new PII detector, or a new output format. See [CONTRIBUTING.md](https://github.com/kirkpatrickprice/PIIDigger/blob/refactor/CONTRIBUTING.md) for the general contribution workflow.

All of PIIDigger's extension points are defined as `Protocol` classes in `piidigger.protocols` — implement the protocol, register the implementation, and the rest of the application picks it up automatically. Each extension point below names its protocol, an existing implementation to copy, and where the registry lives.

## Adding a file handler

Reads one file/archive-member type and yields its text content for scanning.

- **Protocol:** `FileHandler.read(self, source: ScannableItem, config: Config) -> Iterator[str]`
- **Existing examples:** `piidigger/filehandlers/plaintext.py`, `pdf.py`, `docx.py`, `xls.py`, `xlsx.py`
- **Register:** add a `HANDLES = {"ext": [...], "mime": [...]}` dict and a module-level `handler = YourHandler()` instance, then add the module to `piidigger/filehandlers/__init__.py`'s registry loop.
- Use `piidigger.filehandlers._sharedfuncs.ContentBuffer` to stream large files through data handlers in bounded-size batches rather than buffering the whole file in memory — every existing text handler does this the same way.

## Adding a data handler (PII detector)

Scans one piece of text content and returns matches.

- **Protocol:** `DataHandler.find_matches(self, text: str) -> dict[str, set[str]]` — keys are match-type labels (e.g. `"visa"`, `"amex"`), values are the matched strings.
- **Existing examples:** `piidigger/datahandlers/pan.py`, `email.py`
- **Register:** give your handler class a `name` attribute, instantiate it as a module-level `handler = YourHandler()`, and add it to `HANDLER_REGISTRY` in `piidigger/datahandlers/__init__.py`.
- This name becomes the value users put in `data_handlers` in `piidigger.toml` — see [Advanced Configuration](../user-guides/advanced-configuration.md).

## Adding an output sink

Writes findings to a new output format.

- **Protocol:** `OutputSink` — `open()`, `write(record: ResultRecord)`, `close()`
- **Existing examples:** `piidigger/outputhandlers/csv.py`, `json.py`, `text.py`
- **Register:** add the class to `piidigger/outputhandlers/__init__.py`'s `__all__`, and wire it into `_build_sinks()` in `run.py` under the matching `[results].formats` name.

## Adding an archive format

Lists and extracts members from a new archive container type.

- **Protocol:** `ArchiveHandler` — `list_members(archive_path) -> list[MemberInfo]`, `extract_member(archive_path, member_path, dest_dir) -> Path`
- **Existing examples:** `piidigger/archivehandlers/_zip.py`, `_7z.py`, `_tar.py`
- **Register:** give the module an `ARCHIVE_TYPE` string and a `HANDLES = {"ext": [...]}` dict, then add the module to `_MODULES` in `piidigger/archivehandlers/__init__.py`.
- `list_members()` must never extract content to disk — size/ratio/path-traversal safety checks run against this listing before anything is extracted. See [Archive Handling](../user-guides/archive-handling.md) for the safety model new archive handlers need to preserve.

## Adding a new config setting

Settings are plain Pydantic fields on `Config` or one of its nested sub-models (`ArchiveConfig`, `BufferConfig`, `SpreadsheetConfig`, `ResultsConfig`) in `piidigger/models/config.py`. To add one: define the field with a `Field(default=..., ge=...)` (matching the existing settings' style), add it to `_KNOWN_CONFIG_KEYS` so `config validate` can suggest it on a typo, and add its line to `generate_toml_template()`. No other wiring is required for the config file itself — whatever consumes the setting reads it off the `Config`/`WorkerContext` object passed in.

## Reference: the contracts and config model

::: piidigger.protocols
    options:
      show_root_heading: true
      members_order: source

::: piidigger.models.config
    options:
      show_root_heading: true
      members_order: source
