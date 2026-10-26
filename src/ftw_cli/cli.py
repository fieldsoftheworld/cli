"""The ``ftw`` command line."""

import sys
from pathlib import Path
from typing import Annotated

import duckdb
from cyclopts import App, Parameter
from rich.panel import Panel
from rich.table import Table

from ftw_cli import __version__, ftw2e, output
from ftw_cli.cli_args import AreaArg, CrsArg, YearArg, parse_area
from ftw_cli.cli_detect import detect, imagery, models, polygonize, predict
from ftw_cli.progress import bar, spinner

app = App(
    name="ftw",
    version=__version__,
    help="Fields of The World: field boundaries for people and agents.",
)
data = App(name="data", help="Query the FTW Global Data 2nd Edition in place.")
for command in (detect, predict, polygonize, data, imagery, models):
    app.command(command)
app.register_install_completion_command()

EXAMPLES = [
    ("Explore", 'ftw data query "Story County, Iowa"'),
    ("", 'ftw data history "Beauce, France"'),
    ("", 'ftw view "Chartres, France" --out chartres.png'),
    ("Detect", 'ftw detect "Poltava, Ukraine" --year 2024 --out poltava.gpkg'),
]
# Errors that are the input's or the network's fault: one line, no traceback.
EXPECTED = (LookupError, ValueError, OSError, duckdb.Error)


@app.default
def home():
    """What ftw does, with a few commands to start from."""
    if not output.console.is_terminal:
        app.help_print()
        return
    examples = Table.grid(padding=(0, 2))
    for group, command in EXAMPLES:
        examples.add_row(f"[bold #C0D85B]{group}[/]", command)
    output.console.print(
        Panel(
            examples,
            title=f"[bold #C0D85B]ftw[/] Fields of The World [dim]{__version__}[/]",
            subtitle="[dim]ftw --help · ftw --install-completion[/]",
            subtitle_align="left",
            border_style="#22A070",
            padding=(1, 2),
            expand=False,
        )
    )


@app.meta.default
def _meta(
    *tokens: Annotated[str, Parameter(show=False, allow_leading_hyphen=True)],
    json: Annotated[bool | None, Parameter(negative="")] = None,
):
    """
    Parameters
    ----------
    json
        Print JSON. On by default when stdout is not a terminal.
    """
    output.configure(json)
    return app(tokens)


@data.command
def info():
    """Per-year totals: files, parcels, area, and download size."""
    output.emit(ftw2e.info(ftw2e.connect()), title="FTW Global Data 2nd Edition")


@data.command
def query(
    area: AreaArg,
    *,
    year: YearArg = None,
    out: Annotated[
        Path | None,
        Parameter(
            help="Write the fields to .parquet (fiboa), .geojson, .gpkg, or .fgb."
        ),
    ] = None,
    limit: Annotated[
        int | None, Parameter(help="Write at most this many fields.")
    ] = None,
    crs: CrsArg = "4326",
):
    """Summarize the fields in AREA, or write them to a file with --out."""
    box = parse_area(area)
    con = ftw2e.connect()
    year = year or ftw2e.years(con)[-1]
    if out:
        with spinner(f"Writing FTW {year} fields to {out}"):
            result = ftw2e.export(con, box, year, out, limit, crs)
    else:
        with spinner(f"Reading FTW {year} fields"):
            result = ftw2e.summarize(con, box, year)
    output.emit(result, title=f"FTW {year} fields")


@data.command
def history(area: AreaArg):
    """Fields, area, and mean score in AREA for every year."""
    box = parse_area(area)
    with bar("Reading every year") as progress:
        rows = ftw2e.history(box, progress)
    output.emit(rows, title="FTW fields by year")
    output.trend("fields", [r["fields"] for r in rows], [r["year"] for r in rows])


@app.command
def view(
    area: AreaArg,
    *,
    out: Annotated[Path, Parameter(help="PNG file to write.")],
    year: YearArg = None,
    width: Annotated[int, Parameter(help="Image width in pixels.")] = 1200,
    limit: Annotated[
        int, Parameter(help="Draw at most this many fields, largest first.")
    ] = 50_000,
):
    """Draw the fields in AREA to a PNG, colored by model score."""
    from ftw_cli import render

    box = parse_area(area)
    con = ftw2e.connect()
    year = year or ftw2e.years(con)[-1]
    with spinner(f"Drawing FTW {year} fields"):
        fields = ftw2e.fields(con, box, year, limit)
        render.render(fields, box, width).save(out)
    output.emit({"path": str(out), "fields": len(fields), "year": year})


@app.command
def doctor():
    """What is installed and reachable. Attach the output to bug reports."""
    from ftw_cli import doctor as checks

    with spinner("Checking the install and the network"):
        rows = checks.run()
    output.emit(rows, title="ftw doctor")
    if any(r["status"] == "failed" for r in rows):
        raise SystemExit(1)


def _report(kind, error, traceback):
    if issubclass(kind, EXPECTED):
        output.stderr.print(f"[red]error:[/red] {error}")
    else:
        sys.__excepthook__(kind, error, traceback)


def run():
    sys.excepthook = _report
    app.meta()
