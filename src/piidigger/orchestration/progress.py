from __future__ import annotations

import collections
import sys

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskID, TextColumn, TimeElapsedColumn
from rich.table import Table

# Counter keys tracked by the progress display
_COUNTER_KEYS: tuple[str, ...] = (
    "dirs_found",
    "dirs_scanned",
    "files_found",
    "files_scanned",
    "bytes_scanned",
    "results_found",
)

_EVENTS_BUFFER_SIZE = 20


class ProgressDisplay:
    """Two-panel rich.Live progress display owned by the coordinator.

    Top panel: rich.Progress bars for scan counters.
    Bottom panel: scrolling events log (last N warnings/errors).

    Non-TTY mode: start/update/log_event are no-ops; stop() always prints
    a plain-text summary so CI and piped runs still see a result line.
    """

    def __init__(self) -> None:
        self._console = Console(stderr=False)
        self._is_tty: bool = self._console.is_terminal
        self._counters: dict[str, int] = {k: 0 for k in _COUNTER_KEYS}
        self._events: collections.deque[tuple[str, str]] = collections.deque(maxlen=_EVENTS_BUFFER_SIZE)

        # Rich objects — initialised in start(), None until then
        self._progress: Progress | None = None
        self._task_ids: dict[str, TaskID] = {}
        self._events_table: Table | None = None
        self._live: Live | None = None

    def start(self) -> None:
        """Open the rich.Live display.  No-op when not connected to a TTY."""
        if not self._is_tty:
            return

        progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed:>8,}"),
            TimeElapsedColumn(),
            console=self._console,
            expand=True,
        )
        self._progress = progress

        for key in _COUNTER_KEYS:
            label = key.replace("_", " ").title()
            task_id = progress.add_task(label, total=None)
            self._task_ids[key] = task_id

        events_table = Table(show_header=True, header_style="bold", expand=True, box=None)
        events_table.add_column("Level", width=8)
        events_table.add_column("Event")
        self._events_table = events_table

        layout = Layout()
        layout.split_column(
            Layout(progress, name="progress", ratio=2),
            Layout(events_table, name="events", ratio=1),
        )

        live = Live(layout, console=self._console, refresh_per_second=4)
        live.start()
        self._live = live

    def update(self, counters: dict[str, int]) -> None:
        """Increment progress bars by the values in counters.  No-op when not a TTY."""
        for key, val in counters.items():
            self._counters[key] = self._counters.get(key, 0) + val

        progress = self._progress
        if not self._is_tty or progress is None:
            return

        for key, val in counters.items():
            task_id = self._task_ids.get(key)
            if task_id is not None:
                progress.advance(task_id, val)

    def log_event(self, level: str, message: str) -> None:
        """Add a line to the scrolling events panel.  No-op when not a TTY."""
        self._events.append((level, message))

        events_table = self._events_table
        live = self._live
        if not self._is_tty or events_table is None or live is None:
            return

        color = {"ERROR": "red", "WARNING": "yellow", "INFO": "green"}.get(level.upper(), "white")
        events_table.add_row(f"[{color}]{level}[/{color}]", message)

    def stop(self) -> None:
        """Close the rich.Live display and print a plain-text summary to stdout."""
        live = self._live
        if self._is_tty and live is not None:
            live.stop()

        # Always print a summary — visible in non-TTY (CI/piped) and after rich closes
        parts = [f"{k}={v:,}" for k, v in self._counters.items() if v > 0]
        summary = "Scan complete. " + ("  ".join(parts) if parts else "No results.")
        print(summary, file=sys.stdout)  # noqa: T201 — intentional user-facing output
