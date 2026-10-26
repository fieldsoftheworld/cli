"""The ``ftw`` command line."""

from pathlib import Path
from typing import Annotated

from cyclopts import App, Parameter

from ftw_cli import __version__, ftw2e, output
from ftw_cli.cli_args import AreaArg, YearArg, fail, need, parse_area
from ftw_cli.cli_infer import imagery, infer, models
from ftw_cli.progress import bar, spinner

app = App(
    name="ftw",
    version=__version__,
    help="Fields of The World: field boundaries for people and agents.",
)
data = App(
    name="data", help="Query the FTW Global Data 2nd Edition field boundaries in place."
)
app.command(data)
app.command(imagery)
app.command(models)
app.command(infer)
app.register_install_completion_command()

EXAMPLES = [
    ("Explore", 'ftw data query "Story County, Iowa"'),
    ("", 'ftw data history "Beauce, France"'),
    ("", 'ftw view "Chartres, France" --out chartres.png'),
    ("Detect", 'ftw infer all "Poltava, Ukraine" --year 2024 --out-dir poltava'),
    ("Browse", "ftw tui"),
]


@app.default
def home():
    """What ftw does, with a few commands to start from."""
    if not output.console.is_terminal:
        app.help_print()
        return
    from rich.panel import Panel
    from rich.table import Table

    examples = Table.grid(padding=(0, 2))
    for group, command in EXAMPLES:
        examples.add_row(f"[bold #C0D85B]{group}[/]", f"[white]{command}[/]")
    output.console.print(
        Panel(
            examples,
            title=f"[bold #C0D85B]ftw[/] [white]Fields of The World[/] [dim]{__version__}[/]",
            subtitle="[dim]ftw --help for every command · ftw --install-completion[/]",
            subtitle_align="left",
            border_style="#22A070",
            padding=(1, 2),
            expand=False,
        )
    )
    output.console.print(
        "  [#7EC185]Field boundaries for any place on Earth, 2017 to 2025, from your terminal.[/]",
        highlight=False,
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
def files(area: AreaArg, *, year: YearArg = None):
    """List the partition files a query over AREA would read, without reading them."""
    box = parse_area(area)
    con = ftw2e.connect()
    year = year or ftw2e.latest_year(con)
    output.emit(ftw2e.files(con, box, year), title=f"Files for {year}")


@data.command
def query(
    area: AreaArg,
    *,
    year: YearArg = None,
    out: Annotated[
        Path | None,
        Parameter(
            help="Write the fields to this file (.parquet, .geojson, .gpkg, .fgb)."
        ),
    ] = None,
    limit: Annotated[
        int | None, Parameter(help="Write at most this many fields.")
    ] = None,
):
    """Summarize the fields in AREA, or write them to a file with --out."""
    box = parse_area(area)
    con = ftw2e.connect()
    year = year or ftw2e.latest_year(con)
    if out is None:
        with spinner(f"Reading FTW {year} fields"):
            summary = ftw2e.summarize(con, box, year)
        output.emit(summary, title=f"FTW {year} fields")
        return
    try:
        with spinner(f"Writing FTW {year} fields to {out}"):
            output.emit(ftw2e.export(con, box, year, out, limit))
    except (LookupError, ValueError) as e:
        fail(str(e))


@data.command
def history(area: AreaArg):
    """Fields, area, and mean score in AREA for every year, 2017 to the latest."""
    box = parse_area(area)
    with bar("Reading every year") as progress:
        rows = ftw2e.history(box, progress)
    output.emit(
        [{k: r[k] for k in ("year", "fields", "area_ha", "mean_score")} for r in rows],
        title="FTW fields by year",
    )
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
    year = year or ftw2e.latest_year(con)
    with spinner(f"Drawing FTW {year} fields"):
        fields = ftw2e.fields(con, box, year, limit)
        render.render(fields, box, width).save(out)
    output.emit({"path": str(out), "fields": len(fields), "year": year})


@app.command
def tui():
    """Browse field boundaries interactively. Needs the [tui] extra."""
    try:
        from ftw_cli.tui import main
    except ImportError as e:
        need("tui", e)
    main()


def run():
    app.meta()
