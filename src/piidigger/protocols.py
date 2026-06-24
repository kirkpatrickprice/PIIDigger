from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import IO, Protocol, runtime_checkable

from piidigger.models.results import ResultRecord


@runtime_checkable
class DataHandler(Protocol):
    name: str

    def find_matches(self, text: str) -> dict[str, set[str]]: ...


@runtime_checkable
class ScannableItem(Protocol):
    display_path: str
    ext: str
    mime: str | None
    size: int
    depth: int

    def open_stream(self) -> IO[bytes]: ...
    def materialize(self) -> Path: ...


@runtime_checkable
class FileHandler(Protocol):
    def read(self, source: ScannableItem) -> Iterator[str]: ...


@runtime_checkable
class OutputSink(Protocol):
    def open(self) -> None: ...
    def write(self, record: ResultRecord) -> None: ...
    def close(self) -> None: ...
