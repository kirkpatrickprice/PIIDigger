"""Registry-backed validation of handler names in Config.

Covers the four fields that validate against a handler registry, plus the
registry-to-consumer contracts that keep those registries honest:

    results.formats   -> outputhandlers.HANDLER_REGISTRY
    archives.formats  -> archivehandlers.HANDLER_REGISTRY
    include_exts      -> filehandlers.get_supported_exts()
    include_mime      -> filehandlers.get_supported_mimes()
    data_handlers     -> datahandlers.HANDLER_REGISTRY

tests/test_config_model.py and tests/test_archives.py cover the happy path and
the basic reject case for each field.  This module covers what those leave
open: message content, multi-offender reporting, the "all" short-circuit,
case-sensitivity, the from_toml surface, and registry drift.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from piidigger.archivehandlers import HANDLER_REGISTRY as ARCHIVE_REGISTRY
from piidigger.datahandlers import HANDLER_REGISTRY as DATA_HANDLER_REGISTRY
from piidigger.filehandlers import get_supported_exts, get_supported_mimes
from piidigger.models.config import ArchiveConfig, Config, ResultsConfig
from piidigger.outputhandlers import HANDLER_REGISTRY as OUTPUT_REGISTRY

# Each entry: field label -> callable taking a list of names, returning the
# validated model.  Keeps the shared-behaviour tests below field-agnostic, so
# adding a sixth registry-validated field means adding one row here.
_FIELDS: dict[str, Any] = {
    "results.formats": lambda names: ResultsConfig(formats=names),
    "archives.formats": lambda names: ArchiveConfig(formats=names),
    "include_exts": lambda names: Config(start_dirs=[], include_exts=names),
    "include_mime": lambda names: Config(start_dirs=[], include_mime=names),
    "data_handlers": lambda names: Config(start_dirs=[], data_handlers=names),
}

_ALL_FIELDS = list(_FIELDS)


# ---------------------------------------------------------------------------
# Error message content
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("field", _ALL_FIELDS)
def test_unknown_name_appears_in_message(field: str) -> None:
    """The rejected name is quoted back, so the user can find the typo."""
    with pytest.raises(ValidationError) as exc_info:
        _FIELDS[field](["definitely-not-registered"])

    assert "definitely-not-registered" in str(exc_info.value)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "expected_known"),
    [
        ("results.formats", "csv"),
        ("archives.formats", "zip"),
        ("include_exts", ".docx"),
        ("include_mime", "application/pdf"),
        ("data_handlers", "pan"),
    ],
)
def test_message_lists_known_values(field: str, expected_known: str) -> None:
    """The message lists what IS accepted, so the user can self-correct."""
    with pytest.raises(ValidationError) as exc_info:
        _FIELDS[field](["definitely-not-registered"])

    message = str(exc_info.value)
    assert "known:" in message
    assert expected_known in message


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "valid", "invalid"),
    [
        ("results.formats", "csv", "xml"),
        ("archives.formats", "zip", "rar"),
        ("include_exts", ".docx", ".docs"),
        ("include_mime", "application/pdf", "application/x-bogus"),
        ("data_handlers", "pan", "ssn"),
    ],
)
def test_only_the_offender_is_reported(field: str, valid: str, invalid: str) -> None:
    """A valid sibling in the same list is not named as unknown."""
    with pytest.raises(ValidationError) as exc_info:
        _FIELDS[field]([valid, invalid])

    # The offenders are listed before "known:"; slice off that tail so the
    # accepted-values list does not count as a mention of the valid name.
    offenders = str(exc_info.value).split("known:")[0]
    assert invalid in offenders
    assert valid not in offenders


@pytest.mark.unit
@pytest.mark.parametrize("field", _ALL_FIELDS)
def test_all_unknown_names_reported_together(field: str) -> None:
    """Two typos produce one error naming both, not a fix-one-rerun loop."""
    with pytest.raises(ValidationError) as exc_info:
        _FIELDS[field](["bogus-one", "bogus-two"])

    message = str(exc_info.value)
    assert "bogus-one" in message
    assert "bogus-two" in message


# ---------------------------------------------------------------------------
# "all" short-circuit and empty lists
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("field", _ALL_FIELDS)
def test_all_short_circuits_validation_of_siblings(field: str) -> None:
    """An "all" entry returns early, so an unknown sibling is not rejected.

    This pins current behaviour rather than endorsing it: "all" already selects
    every registered handler, so a sibling name changes nothing either way.
    """
    _FIELDS[field](["all", "definitely-not-registered"])


@pytest.mark.unit
@pytest.mark.parametrize("field", _ALL_FIELDS)
def test_empty_list_is_accepted(field: str) -> None:
    """An empty list has no unknown names, so it validates.

    results.formats=[] is a supported "write no output" state (see
    test_run.py::test_build_sinks_no_formats_returns_empty); validation must
    not break it.
    """
    _FIELDS[field]([])


# ---------------------------------------------------------------------------
# Case sensitivity — deliberately asymmetric, pinned here so a change is loud
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "given", "expected"),
    [
        ("results.formats", ["CSV"], ["csv"]),
        ("results.formats", ["Csv", "JSON", "text"], ["csv", "json", "text"]),
        ("archives.formats", ["ZIP"], ["zip"]),
        ("archives.formats", ["Zip", "TAR", "7z"], ["zip", "tar", "7z"]),
    ],
)
def test_format_names_are_normalized_to_lowercase(field: str, given: list[str], expected: list[str]) -> None:
    """Mixed case validates AND is stored lowercased.

    Storing the user's original casing was a silent-failure bug: every consumer
    of these lists matches against the lowercase registry keys, so a stored
    "CSV" validated and then matched nothing.  Normalizing in the validator
    means the stored config is the config that runs.
    """
    assert _FIELDS[field](given).formats == expected


@pytest.mark.unit
@pytest.mark.parametrize("field", ["results.formats", "archives.formats"])
@pytest.mark.parametrize("spelling", ["all", "ALL", "All"])
def test_all_is_recognized_in_any_case(field: str, spelling: str) -> None:
    """ "all" is normalized before the short-circuit, so "ALL" works too.

    Normalization runs first specifically so the short-circuit sees "all".
    Consumers test for the literal lowercase "all" (run._build_sinks,
    _enum_dir), so the stored value must be lowercase as well.
    """
    assert _FIELDS[field]([spelling]).formats == ["all"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "typo"),
    [
        ("results.formats", "XML"),
        ("archives.formats", "RAR"),
    ],
)
def test_unknown_format_is_reported_as_the_user_spelled_it(field: str, typo: str) -> None:
    """Comparison is case-insensitive; the error still echoes the original.

    Reporting a lowercased name back would make the message harder to match
    against what the user actually typed in their TOML file.
    """
    with pytest.raises(ValidationError) as exc_info:
        _FIELDS[field]([typo])

    assert typo in str(exc_info.value).split("known:")[0]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "name"),
    [
        ("include_exts", ".DOCX"),
        ("include_mime", "Application/PDF"),
        ("data_handlers", "PAN"),
    ],
)
def test_extension_mime_and_handler_names_are_case_sensitive(field: str, name: str) -> None:
    """These validators compare exactly, so wrong case is an error.

    Extensions arrive lowercased from the filesystem walk and MIME types are
    lowercase by spec, so exact matching is correct here.
    """
    with pytest.raises(ValidationError):
        _FIELDS[field]([name])


# ---------------------------------------------------------------------------
# Registry drift — every registered name must be accepted by its validator
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("name", sorted(OUTPUT_REGISTRY))
def test_every_registered_result_format_validates(name: str) -> None:
    assert ResultsConfig(formats=[name]).formats == [name]


@pytest.mark.unit
@pytest.mark.parametrize("name", sorted(ARCHIVE_REGISTRY))
def test_every_registered_archive_format_validates(name: str) -> None:
    assert ArchiveConfig(formats=[name]).formats == [name]


@pytest.mark.unit
@pytest.mark.parametrize("name", sorted(DATA_HANDLER_REGISTRY))
def test_every_registered_data_handler_validates(name: str) -> None:
    assert Config(start_dirs=[], data_handlers=[name]).data_handlers == [name]


@pytest.mark.unit
def test_every_supported_ext_validates() -> None:
    exts = get_supported_exts()
    assert exts, "filehandlers registered no extensions"
    assert Config(start_dirs=[], include_exts=exts).include_exts == exts


@pytest.mark.unit
def test_every_supported_mime_validates() -> None:
    mimes = get_supported_mimes()
    assert mimes, "filehandlers registered no MIME types"
    assert Config(start_dirs=[], include_mime=mimes).include_mime == mimes


@pytest.mark.unit
@pytest.mark.parametrize("name", sorted(OUTPUT_REGISTRY))
def test_every_registered_result_format_builds_a_sink(name: str, tmp_path: Path) -> None:
    """A format Config accepts must produce a sink in run.py.

    HANDLER_REGISTRY is the single source of truth for _ALL_FORMATS, but
    _build_sinks names each sink class explicitly.  Adding a registry entry
    without wiring it into _build_sinks would let the format validate and then
    write nothing; this test fails when that happens.
    """
    from piidigger.run import _build_sinks

    config = Config(
        start_dirs=[],
        log_file=tmp_path / "test.log",
        results=ResultsConfig(path=tmp_path / "results", formats=[name]),
    )
    sinks = _build_sinks(config)
    assert len(sinks) == 1, f"format {name!r} validates but builds no sink"
    assert type(sinks[0]) is OUTPUT_REGISTRY[name]


# ---------------------------------------------------------------------------
# from_toml — the path a real user hits
# ---------------------------------------------------------------------------


def _write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(f'start_dirs = ["{tmp_path.as_posix()}"]\n{body}', encoding="utf-8")
    return path


@pytest.mark.unit
@pytest.mark.parametrize(
    ("body", "location", "offender"),
    [
        ('include_exts = [".docs"]\n', "include_exts", ".docs"),
        ('include_mime = ["application/x-bogus"]\n', "include_mime", "application/x-bogus"),
        ('data_handlers = ["ssn"]\n', "data_handlers", "ssn"),
        ('\n[results]\nformats = ["xml"]\n', "results.formats", "xml"),
        ('\n[archives]\nformats = ["rar"]\n', "archives.formats", "rar"),
    ],
)
def test_from_toml_reports_unknown_name(tmp_path: Path, body: str, location: str, offender: str) -> None:
    """from_toml wraps the ValidationError, naming the setting and the value."""
    path = _write_config(tmp_path, body)

    with pytest.raises(ValueError) as exc_info:
        Config.from_toml(path)

    message = str(exc_info.value)
    assert "invalid configuration" in message
    assert f"Invalid value for '{location}'" in message
    assert offender in message


@pytest.mark.unit
def test_from_toml_reports_errors_in_several_sections_at_once(tmp_path: Path) -> None:
    """A root field and a nested one both surface from a single load."""
    path = _write_config(tmp_path, 'include_exts = [".docs"]\n\n[results]\nformats = ["xml"]\n')

    with pytest.raises(ValueError) as exc_info:
        Config.from_toml(path)

    message = str(exc_info.value)
    assert "Invalid value for 'include_exts'" in message
    assert "Invalid value for 'results.formats'" in message


@pytest.mark.unit
def test_from_toml_accepts_registered_names(tmp_path: Path) -> None:
    """The mirror of the reject cases: valid names load without error."""
    path = _write_config(
        tmp_path,
        'include_exts = [".txt"]\ndata_handlers = ["pan"]\n'
        '\n[results]\nformats = ["csv"]\n'
        '\n[archives]\nformats = ["zip"]\n',
    )

    config = Config.from_toml(path)
    assert config.include_exts == [".txt"]
    assert config.data_handlers == ["pan"]
    assert config.results.formats == ["csv"]
    assert config.archives.formats == ["zip"]


# ---------------------------------------------------------------------------
# Regression: mixed-case formats must survive all the way to the consumers
#
# The bug these cover: ResultsConfig accepted "CSV" and stored it unchanged,
# then run._build_sinks intersected case-sensitively against _ALL_FORMATS and
# produced zero sinks.  The scan ran, found PII, and wrote nothing — no error,
# no warning.  Validating the model alone would not have caught it, so these
# tests follow the value through to the code that consumes it.
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("spelling", "expected_sink"),
    [
        ("CSV", "CsvSink"),
        ("Json", "JsonSink"),
        ("TEXT", "TextSink"),
    ],
)
def test_mixed_case_result_format_builds_its_sink(spelling: str, expected_sink: str, tmp_path: Path) -> None:
    from piidigger.run import _build_sinks

    config = Config(
        start_dirs=[],
        log_file=tmp_path / "test.log",
        results=ResultsConfig(path=tmp_path / "results", formats=[spelling]),
    )
    sinks = _build_sinks(config)
    assert len(sinks) == 1, f"format {spelling!r} validates but builds no sink"
    assert type(sinks[0]).__name__ == expected_sink


@pytest.mark.unit
def test_mixed_case_all_builds_every_sink(tmp_path: Path) -> None:
    """_build_sinks tests for the literal "all", so normalization must reach it."""
    from piidigger.run import _build_sinks

    config = Config(
        start_dirs=[],
        log_file=tmp_path / "test.log",
        results=ResultsConfig(path=tmp_path / "results", formats=["ALL"]),
    )
    assert len(_build_sinks(config)) == len(OUTPUT_REGISTRY)


@pytest.mark.unit
@pytest.mark.parametrize("spelling", ["ZIP", "Zip"])
def test_mixed_case_archive_format_is_matched_by_the_worker(spelling: str, tmp_path: Path) -> None:
    """_enum_dir gates archive_type on config.archives.formats.

    It lowercases defensively, so this passed before the fix too.  Kept so the
    archive side is pinned the same way as the results side.
    """
    from piidigger.orchestration.worker._enum_dir import _detect_archive_type

    config = Config(
        start_dirs=[],
        log_file=tmp_path / "test.log",
        archives=ArchiveConfig(formats=[spelling]),
    )
    assert _detect_archive_type("payload.zip", config) == "zip"


@pytest.mark.unit
def test_mixed_case_survives_a_toml_round_trip(tmp_path: Path) -> None:
    """The whole path a user hits: uppercase in the file, sinks at the end."""
    from piidigger.run import _build_sinks

    path = _write_config(tmp_path, '\n[results]\nformats = ["CSV", "TEXT"]\n\n[archives]\nformats = ["ZIP"]\n')

    config = Config.from_toml(path)
    assert config.results.formats == ["csv", "text"]
    assert config.archives.formats == ["zip"]

    config = config.model_copy(update={"results": ResultsConfig(path=tmp_path / "results", formats=["CSV", "TEXT"])})
    assert {type(s).__name__ for s in _build_sinks(config)} == {"CsvSink", "TextSink"}
