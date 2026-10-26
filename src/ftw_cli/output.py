"""Command results as tables for people, or JSON for agents and pipes."""

import json
import sys

from rich.console import Console
from rich.table import Table

console = Console()
stderr = Console(stderr=True)
JSON = False
# Integer columns that are labels, so no thousands separators.
IDENTIFIERS = {"year", "zone"}
SPARKS = "▁▂▃▄▅▆▇█"


def configure(json_mode: bool | None) -> None:
    """JSON when asked, or by default whenever stdout is not a terminal."""
    global JSON
    JSON = json_mode if json_mode is not None else not sys.stdout.isatty()


def emit(data: dict | list[dict], title: str | None = None) -> None:
    if JSON:
        sys.stdout.write(json.dumps(data, default=str) + "\n")
    elif isinstance(data, dict):
        table = Table(title=title, show_header=False, box=None, padding=(0, 2))
        table.add_column(style="#7EC185")
        table.add_column(style="bold")
        for key, value in data.items():
            table.add_row(key, _cell(key, value))
        console.print(table)
    elif not data:
        console.print("[dim]no results[/dim]")
    else:
        table = Table(title=title)
        for column, value in data[0].items():
            numeric = isinstance(value, int | float) and not isinstance(value, bool)
            right = numeric and column not in IDENTIFIERS
            table.add_column(column, justify="right" if right else "left")
        for row in data:
            table.add_row(*(_cell(k, v) for k, v in row.items()))
        console.print(table)


def print_json(data: object) -> None:
    """JSON either way: pretty and colored in a terminal, compact when piped."""
    if JSON:
        sys.stdout.write(json.dumps(data, default=str) + "\n")
    else:
        console.print_json(data=data, default=str)


def trend(label: str, values: list[float], keys: list[object]) -> None:
    """A one-line sparkline of ``values``, for people only."""
    if JSON or len(values) < 2:
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


def _cell(key: str, value: object) -> str:
    if isinstance(value, list | tuple):
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
