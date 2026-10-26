"""Progress on stderr at a terminal; nothing for pipes and agents.

Library functions take an optional ``Progress`` callback, ``progress(done, total)``.
"""

from collections.abc import Callable, Generator
from contextlib import contextmanager

from rich import progress as rp

from ftw_cli import output

Progress = Callable[[int, int | None], None]


@contextmanager
def bar(description: str, unit: str = "items") -> Generator[Progress]:
    """``unit`` is ``items`` (bar and count), ``bytes`` (bar, size, speed), or ``none``."""
    if not output.stderr.is_terminal:
        yield lambda done, total: None
        return
    counts = {
        "items": [rp.MofNCompleteColumn()],
        "bytes": [rp.DownloadColumn(), rp.TransferSpeedColumn()],
    }.get(unit)
    columns: list[rp.ProgressColumn] = [
        rp.SpinnerColumn("dots", style="#C0D85B"),
        rp.TextColumn("{task.description}"),
    ]
    if counts:
        columns += [
            rp.BarColumn(complete_style="#22A070", finished_style="#C0D85B"),
            *counts,
        ]
    with rp.Progress(
        *columns, rp.TimeElapsedColumn(), console=output.stderr, transient=True
    ) as live:
        task = live.add_task(description, total=None)
        yield lambda done, total: live.update(task, completed=done, total=total)


@contextmanager
def spinner(description: str) -> Generator[None]:
    with bar(description, unit="none"):
        yield
