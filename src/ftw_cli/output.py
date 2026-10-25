"""Render command results as tables for people or JSON for agents and pipes."""

import json
import sys

from rich.console import Console
from rich.table import Table

console = Console()
stderr = Console(stderr=True)

# Integer columns that are labels, not quantities, so no thousands separators.
IDENTIFIERS = {"year", "zone"}


class _Mode:
    json = False


def configure(json_mode: bool | None) -> None:
    """JSON when asked, or by default whenever stdout is not a terminal."""
    _Mode.json = json_mode if json_mode is not None else not sys.stdout.isatty()


def emit(data: dict | list[dict], title: str | None = None) -> None:
    if _Mode.json:
        sys.stdout.write(json.dumps(data, default=str) + "\n")
        return
    rows = data if isinstance(data, list) else [data]
    if not rows:
        console.print("[dim]no results[/dim]")
        return
    table = Table(title=title)
    for column in rows[0]:
        table.add_column(str(column))
    for row in rows:
        table.add_row(*(_cell(k, v) for k, v in row.items()))
    console.print(table)


def _cell(key: str, value: object) -> str:
    if isinstance(value, float):
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and key not in IDENTIFIERS
    ):
        return f"{value:,}"
    return "" if value is None else str(value)
