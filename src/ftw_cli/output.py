"""Render command results as tables for people or JSON for agents and pipes."""

import json
import sys

from rich.console import Console
from rich.table import Table

console = Console()
stderr = Console(stderr=True)

# Integer columns that are labels, not quantities, so no thousands separators.
IDENTIFIERS = {"year", "zone"}
SPARKS = "▁▂▃▄▅▆▇█"


class _Mode:
    json = False


def configure(json_mode: bool | None) -> None:
    """JSON when asked, or by default whenever stdout is not a terminal."""
    _Mode.json = json_mode if json_mode is not None else not sys.stdout.isatty()


def emit(data: dict | list[dict], title: str | None = None) -> None:
    if _Mode.json:
        sys.stdout.write(json.dumps(data, default=str) + "\n")
        return
    if isinstance(data, dict):
        console.print(_record(data, title))
        return
    rows = data
    if not rows:
        console.print("[dim]no results[/dim]")
        return
    table = Table(title=title)
    for column, value in rows[0].items():
        numeric = isinstance(value, int | float) and not isinstance(value, bool)
        right = numeric and column not in IDENTIFIERS
        table.add_column(str(column), justify="right" if right else "left")
    for row in rows:
        table.add_row(*(_cell(k, v) for k, v in row.items()))
    console.print(table)


def trend(label: str, values: list[float], keys: list[object]) -> None:
    """A one-line sparkline of ``values`` under a table, for people only."""
    if _Mode.json or len(values) < 2:
        return
    lo, hi = min(values), max(values)
    line = "".join(
        SPARKS[round((v - lo) / (hi - lo) * (len(SPARKS) - 1)) if hi > lo else 0]
        for v in values
    )
    change = f"  {values[-1] / values[0] - 1:+.0%} since {keys[0]}" if values[0] else ""
    console.print(
        f"{label}  {keys[0]} [#22A070]{line}[/] {keys[-1]}[dim]{change}[/dim]"
    )


def _record(data: dict, title: str | None) -> Table:
    """One result as a two-column table, which stays readable however many keys it has."""
    table = Table(title=title, show_header=False, box=None, padding=(0, 2))
    table.add_column(style="#7EC185")
    table.add_column(style="bold")
    for key, value in data.items():
        table.add_row(key, _cell(key, value))
    return table


def _cell(key: str, value: object) -> str:
    if isinstance(value, list | tuple):
        # Coordinates read best on one line; names and ids one per line.
        numbers = all(isinstance(v, int | float) for v in value)
        return (", " if numbers else "\n").join(_cell(key, v) for v in value)
    if isinstance(value, float):
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and key not in IDENTIFIERS
    ):
        return f"{value:,}"
    return "" if value is None else str(value)
