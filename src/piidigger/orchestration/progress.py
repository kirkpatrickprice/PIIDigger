from __future__ import annotations

import collections
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskID, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text

from piidigger.orchestration.counters import (
    BYTES_DONE,
    DIRS_DONE,
    FILES_DONE,
    RESULTS_BY_HANDLER,
    UNSUPPORTED_BY_TYPE,
    unsupported_tally_text,
)

# Counter keys accumulated into self._counters (display values only).
# tasks_completed / tasks_pending are handled separately for ETA and are
# NOT accumulated in _counters.
_COUNTER_KEYS: tuple[str, ...] = (
    "dirs_found",
    "files_found",
    "bytes_found",
    *DIRS_DONE,
    *FILES_DONE,
    *BYTES_DONE,
    "results_found",
)

# Why archive members were rejected: a counter and its words in the summary.
_REJECTED_MEMBERS: tuple[tuple[str, str], ...] = (
    ("archive_members_too_deep", "too deep to reach"),
    ("archive_members_over_total", "over the archive's total size"),
    ("archive_members_limited", "over a member size or count limit"),
    ("archive_members_unsafe", "unsafe"),
    ("archive_members_repeated", "repeated"),
)

_EVENTS_BUFFER_SIZE = 20
_PANEL_BORDER_ROWS = 2
_PANEL_SIDE_COLUMNS = 4  # a border and a space of padding on each side
_PROGRESS_PANEL_ROWS = 5 + _PANEL_BORDER_ROWS
_EVENTS_HEADER_ROWS = 1

# Minimum completed tasks before showing an ETA.  Below this threshold the
# rate estimate is too noisy, and scans that finish this quickly are already
# done before the user cares about a countdown.
_ETA_MIN_COMPLETED: int = 200


def _fmt_bytes(n: int) -> str:
    """Format a byte count as a human-readable string (e.g. '1.2 MB')."""
    value: float = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(value) < 1024.0:
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} EB"


@dataclass(frozen=True)
class IncompleteWork:
    """Work a scan did not finish, for the end-of-scan summary.

    Each field counts files, folders or archive members:

    * failed: the handler returned an error, for example access denied or an
      unreadable file.
    * timed_out: ran past its deadline and was stopped.
    * abandoned: crashed its worker.  A file or folder is abandoned after
      crashing on every attempt the retry budget allowed.  An archive member is
      dropped after crashing once, so the rest of its batch can carry on.
    * over_memory: pushed its worker past max_worker_memory_mb, so the worker
      was stopped.  Not retried, like a timeout.
    * unfinished: still outstanding when the scan stopped, usually because it
      was interrupted.

    A dataclass rather than a Pydantic model: the coordinator computes every
    count from its own bookkeeping.
    """

    failed: int = 0
    timed_out: int = 0
    abandoned: int = 0
    over_memory: int = 0
    unfinished: int = 0

    @property
    def total(self) -> int:
        return self.failed + self.timed_out + self.abandoned + self.over_memory + self.unfinished


@dataclass(frozen=True, slots=True)
class ResultsFile:
    """What one results file received, for the end-of-scan check against the results found.

    * path: the file, as its sink names it.
    * written: results that reached it, counted as result_counters() counts them.
    * problem: why it is incomplete, as the summary words it, or None if it is complete.

    A dataclass rather than a Pydantic model: GuardedSink counts every field
    itself, in the coordinator process.
    """

    path: str
    written: int
    problem: str | None = None


def _row(count: int, text: str) -> str:
    """One line of a summary block: the count in its own column, then what it counts."""
    return f"{count:>9,}  {text}"


def _plural(count: int, singular: str, plural: str) -> str:
    return singular if count == 1 else plural


def describe_incomplete(work: IncompleteWork) -> str | None:
    """A summary row for the work that did not finish and why, or None if nothing.

    Failed includes a file read only in part, whose findings are kept, so the row
    does not claim that any of this was skipped outright.
    """
    if not work.total:
        return None
    reasons = [
        (work.failed, _plural(work.failed, "error", "errors")),
        (work.timed_out, "timed out"),
        (work.abandoned, "crashed a worker"),
        (work.over_memory, "over the memory limit"),
        (work.unfinished, "unfinished when the scan stopped"),
    ]
    detail = ", ".join(f"{n:,} {why}" for n, why in reasons if n)
    noun = _plural(work.total, "file, folder or archive member", "files, folders or archive members")
    return _row(work.total, f"{noun}: {detail}")


def _fmt_duration(seconds: float) -> str:
    """Format a duration in seconds as H:MM:SS, for the end-of-scan summary."""
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def _fmt_eta(seconds: float) -> str:
    """Format a duration in seconds as ~H:MM:SS or ~M:SS."""
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"~{h}:{m:02d}:{s:02d}"
    return f"~{m}:{s:02d}"


class ProgressDisplay:
    """Compact rich.Live progress display owned by the coordinator.

    Uses rich.console.Group to stack elements without fixed space allocation:
      - Three progress bars: Dirs, Files, Bytes (scanned / found  pct%)
      - One text counter:    Results Found (monotonically increasing)
      - One ETA row:         task-rate-based estimated time remaining
      - Events log:          last N warnings/errors, rebuilt on each event

    ETA is computed from completed-task rate (all task types combined) and is
    suppressed until _ETA_MIN_COMPLETED tasks have finished so the early
    estimate is meaningful.

    Non-TTY mode: start/update/log_event are no-ops; stop() always prints
    a plain-text summary so CI and piped runs still see a result line.
    """

    def __init__(self, *, log_file: Path | None = None) -> None:
        self._log_file = log_file
        self._console = Console(stderr=False)
        self._is_tty: bool = self._console.is_terminal
        self._counters: dict[str, int] = {k: 0 for k in _COUNTER_KEYS}
        self._events: collections.deque[tuple[str, str]] = collections.deque(maxlen=_EVENTS_BUFFER_SIZE)
        self._startup_lines: list[str] = []

        # ETA state — updated by update() from tasks_completed / tasks_pending
        # counters emitted by the coordinator.  Not stored in _counters because
        # tasks_pending is a snapshot (not cumulative) and neither key is a
        # display counter.
        self._tasks_completed: int = 0
        self._tasks_pending: int = 0
        # Incomplete work.  Failures arrive through update(); the rest is only
        # known once the run ends, via report_incomplete().
        self._tasks_failed: int = 0
        self._timed_out: int = 0
        self._abandoned: int = 0
        self._over_memory: int = 0
        self._unfinished: int = 0
        self._interrupted: bool = False
        self._workers_failed: bool = False
        # What each results file received, known once the sinks are closed.
        self._results_files: list[ResultsFile] = []
        self._scan_start: float = time.monotonic()
        # Set by the first summary_lines() call, so the log and the screen
        # report the same run time.
        self._scan_end: float | None = None

        self._bars: Progress | None = None
        self._text: Progress | None = None
        self._eta_row: Progress | None = None
        self._bar_task_ids: dict[str, TaskID] = {}
        self._text_task_ids: dict[str, TaskID] = {}
        self._eta_task_id: TaskID | None = None
        self._live: Live | None = None

    def _build_events_table(self) -> Table:
        """Build a fresh Table from the bounded events deque."""
        table = Table(show_header=True, header_style="bold", expand=True, box=None)
        table.add_column("Level", width=8)
        table.add_column("Event", ratio=1, no_wrap=True, overflow="ellipsis")
        for level, message in self._visible_events():
            color = {"ERROR": "red", "WARNING": "yellow", "INFO": "green"}.get(level.upper(), "white")
            # Text, not a str: a message is plain text (often a path), and a
            # str cell is parsed as markup, so "\[" or "[draft]" would be mangled.
            table.add_row(f"[{color}]{level}[/{color}]", Text(message))
        return table

    def _config_text(self) -> Text:
        # emoji=False: a ":name:" in a path is not an emoji code.
        return Text.from_markup("\n".join(self._startup_lines), emoji=False) if self._startup_lines else Text()

    def _config_rows(self) -> int:
        """Rows the config panel's text takes, since a long path wraps."""
        if not self._startup_lines:
            return 0
        return len(self._config_text().wrap(self._console, max(1, self._console.width - _PANEL_SIDE_COLUMNS)))

    def _visible_events(self) -> list[tuple[str, str]]:
        """Newest events that fit below the config and progress panels.

        A display taller than the terminal cannot be redrawn in place, so the
        whole screen flickers on every refresh.
        """
        used = self._config_rows() + _PANEL_BORDER_ROWS + _PROGRESS_PANEL_ROWS
        room = self._console.height - used - _PANEL_BORDER_ROWS - _EVENTS_HEADER_ROWS
        return list(self._events)[-room:] if room > 0 else []

    def _compute_eta(self) -> str:
        """Return a human-readable ETA string based on task completion rate.

        Returns '--:--' until _ETA_MIN_COMPLETED tasks have finished.
        The formula is: ETA = elapsed * pending / completed, where 'pending'
        is the snapshot from the last coordinator update.
        """
        if self._tasks_completed < _ETA_MIN_COMPLETED:
            return "--:--"
        elapsed = time.monotonic() - self._scan_start
        if elapsed <= 0 or self._tasks_completed == 0:
            return "--:--"
        pending = self._tasks_pending
        if pending == 0:
            return "~0:00"
        return _fmt_eta(elapsed * pending / self._tasks_completed)

    def _build_config_panel(self) -> Panel:
        return Panel(self._config_text(), title="[bold]Scan Configuration[/bold]", border_style="blue", expand=True)

    def _build_progress_panel(self) -> Panel:
        bars = self._bars
        text = self._text
        eta_row = self._eta_row
        if bars is None or text is None or eta_row is None:
            return Panel("", title="[bold]Progress[/bold]", border_style="blue", expand=True)
        return Panel(Group(bars, text, eta_row), title="[bold]Progress[/bold]", border_style="blue", expand=True)

    def _build_events_panel(self) -> Panel:
        return Panel(self._build_events_table(), title="[bold]Events[/bold]", border_style="blue", expand=True)

    def _build_live_renderable(self) -> Group:
        return Group(
            self._build_config_panel(),
            self._build_progress_panel(),
            self._build_events_panel(),
        )

    def _rebuild_live(self) -> None:
        """Replace the Live renderable with fresh panels; no-op if the display is not active."""
        live = self._live
        bars = self._bars
        text = self._text
        eta_row = self._eta_row
        if not self._is_tty or live is None or bars is None or text is None or eta_row is None:
            return
        live.update(self._build_live_renderable())

    def set_startup_info(self, lines: list[str]) -> None:
        """Populate the static configuration panel.  No-op when not a TTY."""
        self._startup_lines = list(lines)
        self._rebuild_live()

    def start(self) -> None:
        """Open the rich.Live display.  No-op when not connected to a TTY."""
        if not self._is_tty:
            return

        self._scan_start = time.monotonic()

        # Progress bars for paired (scanned / found) counters.
        # Each bar stores its display label in task.fields["label"] so dirs/files
        # can show comma-formatted integers while bytes shows human-readable sizes.
        bars = Progress(
            SpinnerColumn(),
            TextColumn("[bold]{task.description:<8}"),
            BarColumn(),
            TextColumn("{task.fields[label]}"),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=self._console,
            expand=True,
        )
        self._bars = bars
        _int_label = f"{'0':>8} / {'0':>8}"
        _byte_label = f"{'0.0 B':>8} / {'0.0 B':>8}"
        self._bar_task_ids["dirs"] = bars.add_task("Dirs", total=0, completed=0, label=_int_label)
        self._bar_task_ids["files"] = bars.add_task("Files", total=0, completed=0, label=_int_label)
        self._bar_task_ids["bytes"] = bars.add_task("Bytes", total=0, completed=0, label=_byte_label)

        # Spinner-only counter for results (no upper bound, no bar needed).
        text = Progress(
            SpinnerColumn(),
            TextColumn("[bold]{task.description:<16}"),
            TextColumn("{task.completed:>12,.0f}"),
            console=self._console,
            expand=True,
        )
        self._text = text
        self._text_task_ids["results_found"] = text.add_task("Results Found", total=None)

        # Single ETA row — task-rate-based; shows "--:--" until the threshold
        # is reached, then "~H:MM:SS" or "~M:SS".
        eta_row = Progress(
            SpinnerColumn(),
            TextColumn("[bold]{task.description:<16}"),
            TextColumn("{task.fields[label]}"),
            console=self._console,
            expand=True,
        )
        self._eta_row = eta_row
        self._eta_task_id = eta_row.add_task("ETA", total=None, label="--:--")

        live = Live(
            self._build_live_renderable(),
            console=self._console,
            refresh_per_second=2,
            screen=True,
        )
        live.start()
        self._live = live

    def update(self, counters: dict[str, int]) -> None:
        """Accumulate counters and refresh the display.  No-op when not a TTY."""
        # tasks_completed and tasks_failed are cumulative; tasks_pending is a
        # snapshot — replace, not add.  None of them belongs in self._counters:
        # they are not display values.
        if "tasks_completed" in counters:
            self._tasks_completed += counters["tasks_completed"]
        if "tasks_pending" in counters:
            self._tasks_pending = counters["tasks_pending"]
        if "tasks_failed" in counters:
            self._tasks_failed += counters["tasks_failed"]

        for key, val in counters.items():
            if key not in ("tasks_completed", "tasks_pending", "tasks_failed"):
                self._counters[key] = self._counters.get(key, 0) + val

        bars = self._bars
        text = self._text
        eta_row = self._eta_row
        eta_task_id = self._eta_task_id
        if not self._is_tty or bars is None or text is None or eta_row is None or eta_task_id is None:
            return

        dirs_found = self._counters["dirs_found"]
        dirs_done = sum(self._counters[k] for k in DIRS_DONE)
        files_found = self._counters["files_found"]
        files_done = sum(self._counters[k] for k in FILES_DONE)
        bytes_found = self._counters["bytes_found"]
        bytes_done = sum(self._counters[k] for k in BYTES_DONE)

        # Use absolute values so the bar always reflects total state, not deltas.
        # max() guards the brief window where done could equal found before
        # the next batch of found values arrives.
        bars.update(
            self._bar_task_ids["dirs"],
            total=max(dirs_found, dirs_done),
            completed=dirs_done,
            label=f"{dirs_done:>8,} / {dirs_found:>8,}",
        )
        bars.update(
            self._bar_task_ids["files"],
            total=max(files_found, files_done),
            completed=files_done,
            label=f"{files_done:>8,} / {files_found:>8,}",
        )
        bars.update(
            self._bar_task_ids["bytes"],
            total=max(bytes_found, bytes_done),
            completed=bytes_done,
            label=f"{_fmt_bytes(bytes_done):>8} / {_fmt_bytes(bytes_found):>8}",
        )

        for key, task_id in self._text_task_ids.items():
            delta = counters.get(key, 0)
            if delta:
                text.advance(task_id, delta)

        eta_row.update(eta_task_id, label=self._compute_eta())

    def log_event(self, level: str, message: str) -> None:
        """Append an event and rebuild the events panel.  No-op when not a TTY."""
        self._events.append((level, message))
        self._rebuild_live()

    def report_incomplete(
        self,
        *,
        timed_out: int,
        abandoned: int,
        unfinished: int,
        interrupted: bool,
        workers_failed: bool = False,
        over_memory: int = 0,
    ) -> None:
        """Record the work the run could not finish, for the summary stop() prints.

        Called once by the coordinator, after the drain loop and before stop().
        Failures are not passed here: they are counted as their results arrive.
        """
        self._timed_out = timed_out
        self._abandoned = abandoned
        self._over_memory = over_memory
        self._unfinished = unfinished
        self._interrupted = interrupted
        self._workers_failed = workers_failed

    def report_results_files(self, files: list[ResultsFile]) -> None:
        """Record what each results file received, for the summary stop() prints.

        Called by the coordinator after it closes the sinks, so a file that
        fails while closing is included.  That is during teardown, so it is not
        part of report_incomplete().
        """
        self._results_files = list(files)

    @property
    def short_results_files(self) -> tuple[str, ...]:
        """Each results file that failed or did not receive every result found."""
        total = self._counters["results_found"]
        return tuple(f.path for f in self._results_files if f.problem is not None or f.written != total)

    @property
    def incomplete(self) -> IncompleteWork:
        """Everything the scan did not finish, as currently known."""
        return IncompleteWork(
            failed=self._tasks_failed,
            timed_out=self._timed_out,
            abandoned=self._abandoned,
            over_memory=self._over_memory,
            unfinished=self._unfinished,
        )

    def _by_prefix(self, prefix: str) -> dict[str, int]:
        """The counters kept per kind under one prefix, keyed by kind."""
        return {k.removeprefix(prefix): v for k, v in self._counters.items() if k.startswith(prefix) and v > 0}

    def _describe_results(self) -> str:
        total = self._counters["results_found"]
        if not total:
            return "No results found."
        by_handler = sorted(self._by_prefix(RESULTS_BY_HANDLER).items(), key=lambda kv: kv[1], reverse=True)
        detail = ", ".join(f"{count:,} {handler}" for handler, count in by_handler)
        found = f"Found {total:,} {_plural(total, 'result', 'results')}"
        return f"{found}: {detail}." if detail else f"{found}."

    def _describe_scanned(self) -> str | None:
        dirs = self._counters["dirs_scanned"]
        files = self._counters["files_scanned"]
        size = self._counters["bytes_scanned"]
        parts = []
        if dirs:
            parts.append(f"{dirs:,} {_plural(dirs, 'folder', 'folders')}")
        if files:
            parts.append(f"{files:,} {_plural(files, 'file', 'files')}")
        if size:
            parts.append(_fmt_bytes(size))
        return ", ".join(parts) or None

    def _not_fully_scanned_rows(self) -> list[str]:
        """One row per kind of content that was not read and might hold PII."""
        rows = []
        if (incomplete := describe_incomplete(self.incomplete)) is not None:
            rows.append(incomplete)
        count = self._counters.get
        if unreadable := count("files_unreadable", 0):
            noun = _plural(unreadable, "file", "files")
            rows.append(
                _row(
                    unreadable,
                    f'{noun} not read: text encoding could not be detected (search the log for "text encoding")',
                )
            )
        if no_text := count("pdfs_no_text", 0):
            noun = _plural(no_text, "PDF", "PDFs")
            rows.append(_row(no_text, f'{noun} with no extractable text (search the log for "no extractable text")'))
        rejected = [(count(key, 0), words) for key, words in _REJECTED_MEMBERS]
        if total := sum(n for n, _ in rejected):
            detail = ", ".join(f"{n:,} {words}" for n, words in rejected if n)
            rows.append(_row(total, f"archive {_plural(total, 'member', 'members')} rejected: {detail}"))
        if encrypted := count("archive_members_encrypted", 0):
            rows.append(_row(encrypted, f"archive {_plural(encrypted, 'member', 'members')} encrypted"))
        if nested := count("archive_members_nested", 0):
            rows.append(_row(nested, f"nested {_plural(nested, 'archive', 'archives')} not opened"))
        return rows

    def _skipped_by_design_rows(self) -> list[str]:
        """Rows for what PIIDigger has no reader for, or was told to leave out."""
        rows = []
        if files := sum(self._by_prefix(UNSUPPORTED_BY_TYPE).values()):
            rows.append(_row(files, f"{_plural(files, 'file', 'files')} of unsupported types"))
        if members := self._counters.get("archive_members_unsupported", 0):
            rows.append(_row(members, f"archive {_plural(members, 'member', 'members')} of unsupported types"))
        return rows

    def summary_lines(self, *, for_log: bool = False) -> list[tuple[int, str]]:
        """The end-of-scan summary, as (logging level, text) pairs, one fact per line.

        stop() prints these, and the coordinator logs them before the log
        listener stops, so the screen and the log cannot drift apart.

        The first lines give the run time, the results and what was scanned.  A
        "Not fully scanned" block appears only for content that was not read and
        might hold PII, so a partial scan cannot be mistaken for a full one.  A
        "Skipped by design" block stays apart from it, so an unsupported type does
        not read as a problem.  Rows with a zero count are left out.

        Last comes the check of each results file against the results found.  When
        every file received them all, that is one line.  Otherwise a "Results files
        incomplete" block lists every file with its count and what it is missing.

        for_log leaves out the screen's "See the log for details." pointer and its
        blank lines, and adds the unsupported file types, which are too long a
        list for the screen.
        """
        if self._scan_end is None:
            self._scan_end = time.monotonic()
        see_log = "" if for_log else " See the log for details."
        if not for_log and self._log_file is not None:
            see_log = f" See the log for details: {self._log_file}"
        run_time = _fmt_duration(self._scan_end - self._scan_start)

        incomplete = self.incomplete
        finished = False
        if self._interrupted:
            heading = f"Scan interrupted after {run_time}."
        elif self._workers_failed:
            heading = f"Scan stopped early after {run_time}: workers could not start."
        elif incomplete.unfinished:
            heading = f"Scan stopped early after {run_time}."
        else:
            heading = f"Scan complete in {run_time}."
            finished = True
        level = logging.INFO if finished else logging.WARNING

        lines = [(level, f"{heading} {self._describe_results()}")]
        if (scanned := self._describe_scanned()) is not None:
            lines.append((level, f"  Scanned: {scanned}"))

        def block(block_level: int, title: str, rows: list[str]) -> None:
            if not rows:
                return
            if not for_log:
                lines.append((block_level, ""))
            lines.append((block_level, title))
            lines.extend((block_level, row) for row in rows)

        block(logging.WARNING, f"Not fully scanned.{see_log}", self._not_fully_scanned_rows())
        block(logging.INFO, "Skipped by design:", self._skipped_by_design_rows())
        if for_log and (by_type := self._by_prefix(UNSUPPORTED_BY_TYPE)):
            lines.append((logging.INFO, f"Files of unsupported types, by type: {unsupported_tally_text(by_type)}"))
        found = self._counters["results_found"]
        results = _plural(found, "result", "results")
        if self.short_results_files:
            title = f"Results files incomplete ({found:,} {results} found).{see_log}"
            block(logging.WARNING, title, [self._results_file_row(f, found) for f in self._results_files])
        elif found and self._results_files:
            if not for_log:
                lines.append((logging.INFO, ""))
            lines.append((logging.INFO, f"Results files: all {found:,} {results} written to every results file."))
        return lines

    @staticmethod
    def _results_file_row(results_file: ResultsFile, found: int) -> str:
        """One results file's count, and what it is missing, if anything."""
        missing = found - results_file.written
        if results_file.problem is not None:
            shortfall = f"{missing:,} missing: " if missing > 0 else ""
            note = f"  ({shortfall}{results_file.problem})"
        elif missing > 0:
            note = f"  ({missing:,} missing)"
        elif missing < 0:
            note = f"  ({-missing:,} more than found)"
        else:
            note = ""
        return _row(results_file.written, f"{results_file.path}{note}")

    def stop(self, *, summary: bool = True) -> None:
        """Close the rich.Live display and print the summary_lines() to stdout.

        Pass summary=False when the scan failed before it began.  Any summary
        would then claim a scan that never ran.
        """
        live = self._live
        if self._is_tty and live is not None:
            live.stop()
        if not summary:
            return

        for _, line in self.summary_lines():
            if self._is_tty:
                # A path is plain text, and must stay whole to be copied.
                self._console.print(line, markup=False, emoji=False, soft_wrap=True)
            else:
                print(line, file=sys.stdout)  # noqa: T201 — intentional user-facing output
