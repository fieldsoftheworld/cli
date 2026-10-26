"""Progress on stderr for people at a terminal, and nothing for pipes and agents.

Library functions take an optional ``progress(done, total)`` callback; ``bar`` makes one.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager

from rich.progress import (
    BarColumn,
    DownloadColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TransferSpeedColumn,
)

from ftw_cli import output


@contextmanager
def bar(
    description: str, unit: str = "items"
) -> Iterator[Callable[[int, int | None], None]]:
    """``unit`` is ``"items"`` (bar and count), ``"bytes"`` (bar, size, speed), or ``"none"``."""
    if not output.stderr.is_terminal:
        yield lambda done, total: None
        return
    counts = {
        "items": [MofNCompleteColumn()],
        "bytes": [DownloadColumn(), TransferSpeedColumn()],
    }.get(unit)
    columns = [SpinnerColumn("dots", style="#C0D85B"), TextColumn("{task.description}")]
    if counts:
        columns += [
            BarColumn(complete_style="#22A070", finished_style="#C0D85B"),
            *counts,
        ]
    with Progress(
        *columns, TimeElapsedColumn(), console=output.stderr, transient=True
    ) as live:
        task = live.add_task(description, total=None)
        yield lambda done, total: live.update(task, completed=done, total=total)


@contextmanager
def spinner(description: str) -> Iterator[None]:
    with bar(description, unit="none"):
        yield
