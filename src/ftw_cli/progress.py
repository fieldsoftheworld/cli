"""Progress bars on stderr for people at a terminal, and nothing for pipes and agents.

Library functions take an optional ``progress(done, total)`` callback and know nothing
about drawing; the CLI passes one from ``bar`` and the TUI passes its own.
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

Callback = Callable[[int, int | None], None]


@contextmanager
def bar(description: str, *, unit: str = "items") -> Iterator[Callback]:
    """Yield a ``progress(done, total)`` callback.

    ``unit`` is ``"items"`` (a bar and a count), ``"bytes"`` (a bar, sizes, and speed), or
    ``"none"`` (just the spinner and elapsed time).
    """
    if not output.stderr.is_terminal:
        yield lambda done, total: None
        return
    if unit == "bytes":
        counts = [_bar(), DownloadColumn(), TransferSpeedColumn()]
    elif unit == "items":
        counts = [_bar(), MofNCompleteColumn()]
    else:
        counts = []
    columns = [
        SpinnerColumn("dots", style="#C0D85B"),
        TextColumn("{task.description}"),
        *counts,
        TimeElapsedColumn(),
    ]
    with Progress(*columns, console=output.stderr, transient=True) as live:
        task = live.add_task(description, total=None)
        yield lambda done, total: live.update(task, completed=done, total=total)


@contextmanager
def spinner(description: str) -> Iterator[None]:
    """A spinner on stderr while a call with no measurable progress runs."""
    with bar(description, unit="none"):
        yield


def _bar() -> BarColumn:
    return BarColumn(complete_style="#22A070", finished_style="#C0D85B")
