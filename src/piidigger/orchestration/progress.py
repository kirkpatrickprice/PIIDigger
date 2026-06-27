from __future__ import annotations

import collections
import sys

from rich.console import Console, Group
from rich.live import Live
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskID, TextColumn, TimeElapsedColumn
from rich.table import Table

# Counter keys accumulated by the progress display
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


class ProgressDisplay:
    """Compact rich.Live progress display owned by the coordinator.

    Uses rich.console.Group to stack elements without fixed space allocation:
      - Three progress bars: Dirs, Files, Bytes (scanned / found  pct%)
      - One text counter:    Results Found (monotonically increasing)
      - Events log:          last N warnings/errors, rebuilt on each event

    Non-TTY mode: start/update/log_event are no-ops; stop() always prints
    a plain-text summary so CI and piped runs still see a result line.
    """

    def __init__(self) -> None:
        self._console = Console(stderr=False)
        self._is_tty: bool = self._console.is_terminal
        self._counters: dict[str, int] = {k: 0 for k in _COUNTER_KEYS}
        self._events: collections.deque[tuple[str, str]] = collections.deque(maxlen=_EVENTS_BUFFER_SIZE)

        self._bars: Progress | None = None
        self._text: Progress | None = None
        self._bar_task_ids: dict[str, TaskID] = {}
        self._text_task_ids: dict[str, TaskID] = {}
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

    def start(self) -> None:
        """Open the rich.Live display.  No-op when not connected to a TTY."""
        if not self._is_tty:
            return

        # Progress bars for paired (scanned / found) counters.
        bars = Progress(
            SpinnerColumn(),
            TextColumn("[bold]{task.description:<8}"),
            BarColumn(),
            TextColumn("{task.completed:>8,.0f} / {task.total:>8,.0f}"),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=self._console,
            expand=True,
        )
        self._bars = bars
        self._bar_task_ids["dirs"] = bars.add_task("Dirs", total=0, completed=0)
        self._bar_task_ids["files"] = bars.add_task("Files", total=0, completed=0)
        self._bar_task_ids["bytes"] = bars.add_task("Bytes", total=0, completed=0)

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

        # Group stacks renderables with no fixed space allocation — elements
        # sit immediately below each other with no gap.
        live = Live(
            Group(bars, text, self._build_events_table()),
            console=self._console,
            refresh_per_second=4,
        )
        live.start()
        self._live = live

    def update(self, counters: dict[str, int]) -> None:
        """Accumulate counters and refresh the bars.  No-op when not a TTY."""
        for key, val in counters.items():
            self._counters[key] = self._counters.get(key, 0) + val

        bars = self._bars
        text = self._text
        if not self._is_tty or bars is None or text is None:
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
        bars.update(self._bar_task_ids["dirs"], total=max(dirs_found, dirs_scanned), completed=dirs_scanned)
        bars.update(self._bar_task_ids["files"], total=max(files_found, files_scanned), completed=files_scanned)
        bars.update(self._bar_task_ids["bytes"], total=max(bytes_found, bytes_scanned), completed=bytes_scanned)

        for key, task_id in self._text_task_ids.items():
            delta = counters.get(key, 0)
            if delta:
                text.advance(task_id, delta)

    def log_event(self, level: str, message: str) -> None:
        """Append an event and rebuild the events table.  No-op when not a TTY."""
        self._events.append((level, message))

        live = self._live
        bars = self._bars
        text = self._text
        if not self._is_tty or live is None or bars is None or text is None:
            return

        # Rebuild the Group so the events table reflects the current deque
        # (bounded to _EVENTS_BUFFER_SIZE — oldest entries are dropped).
        live.update(Group(bars, text, self._build_events_table()))

    def stop(self) -> None:
        """Close the rich.Live display and print a plain-text summary to stdout."""
        live = self._live
        if self._is_tty and live is not None:
            live.stop()

        if not self._is_tty:
            parts = [f"{k}={v:,}" for k, v in self._counters.items() if v > 0]
            summary = "Scan complete. " + ("  ".join(parts) if parts else "No results.")
            print(summary, file=sys.stdout)  # noqa: T201 — intentional user-facing output
