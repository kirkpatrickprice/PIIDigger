from __future__ import annotations

import collections
import sys
import time

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskID, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text

# Counter keys accumulated into self._counters (display values only).
# tasks_completed / tasks_pending are handled separately for ETA and are
# NOT accumulated in _counters.
_COUNTER_KEYS: tuple[str, ...] = (
    "dirs_found",
    "dirs_scanned",
    "files_found",
    "files_scanned",
    "bytes_found",
    "bytes_scanned",
    "results_found",
)

_EVENTS_BUFFER_SIZE = 20

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

    def __init__(self) -> None:
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
        self._scan_start: float = time.monotonic()

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
        table.add_column("Event", ratio=1)
        for level, message in self._events:
            color = {"ERROR": "red", "WARNING": "yellow", "INFO": "green"}.get(level.upper(), "white")
            table.add_row(f"[{color}]{level}[/{color}]", message)
        return table

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
        content = Text.from_markup("\n".join(self._startup_lines)) if self._startup_lines else Text()
        return Panel(content, title="[bold]Scan Configuration[/bold]", border_style="blue", expand=True)

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
            refresh_per_second=4,
        )
        self._console.clear()
        live.start()
        self._live = live

    def update(self, counters: dict[str, int]) -> None:
        """Accumulate counters and refresh the display.  No-op when not a TTY."""
        # tasks_completed is cumulative; tasks_pending is a snapshot — replace,
        # not add.  Neither belongs in self._counters (not display values).
        if "tasks_completed" in counters:
            self._tasks_completed += counters["tasks_completed"]
        if "tasks_pending" in counters:
            self._tasks_pending = counters["tasks_pending"]

        for key, val in counters.items():
            if key not in ("tasks_completed", "tasks_pending"):
                self._counters[key] = self._counters.get(key, 0) + val

        bars = self._bars
        text = self._text
        eta_row = self._eta_row
        eta_task_id = self._eta_task_id
        if not self._is_tty or bars is None or text is None or eta_row is None or eta_task_id is None:
            return

        dirs_found = self._counters["dirs_found"]
        dirs_scanned = self._counters["dirs_scanned"]
        files_found = self._counters["files_found"]
        files_scanned = self._counters["files_scanned"]
        bytes_found = self._counters["bytes_found"]
        bytes_scanned = self._counters["bytes_scanned"]

        # Use absolute values so the bar always reflects total state, not deltas.
        # max() guards the brief window where scanned could equal found before
        # the next batch of found values arrives.
        bars.update(
            self._bar_task_ids["dirs"],
            total=max(dirs_found, dirs_scanned),
            completed=dirs_scanned,
            label=f"{dirs_scanned:>8,} / {dirs_found:>8,}",
        )
        bars.update(
            self._bar_task_ids["files"],
            total=max(files_found, files_scanned),
            completed=files_scanned,
            label=f"{files_scanned:>8,} / {files_found:>8,}",
        )
        bars.update(
            self._bar_task_ids["bytes"],
            total=max(bytes_found, bytes_scanned),
            completed=bytes_scanned,
            label=f"{_fmt_bytes(bytes_scanned):>8} / {_fmt_bytes(bytes_found):>8}",
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

    def stop(self) -> None:
        """Close the rich.Live display and print a plain-text summary to stdout."""
        live = self._live
        if self._is_tty and live is not None:
            live.stop()

        parts = []
        for k, v in self._counters.items():
            if v <= 0:
                continue
            parts.append(f"{k}={_fmt_bytes(v)}" if "bytes" in k else f"{k}={v:,}")

        summary = "Scan complete. " + ("  ".join(parts) if parts else "No results.")
        if self._is_tty:
            self._console.print(summary)
        else:
            print(summary, file=sys.stdout)  # noqa: T201 — intentional user-facing output
