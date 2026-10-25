"""The ``ftw`` command line."""

from pathlib import Path
from typing import Annotated

from cyclopts import App, Parameter

from ftw_cli import __version__, ftw2e, output
from ftw_cli.cli_args import BBoxArg, YearArg, fail, need, parse_bbox
from ftw_cli.cli_infer import imagery, infer, models

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
def files(*, bbox: BBoxArg, year: YearArg = None):
    """List the partition files a query over BBOX would read, without reading them."""
    con = ftw2e.connect()
    year = year or ftw2e.latest_year(con)
    output.emit(ftw2e.files(con, parse_bbox(bbox), year), title=f"Files for {year}")


@data.command
def query(
    *,
    bbox: BBoxArg,
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
    """Summarize the fields in BBOX, or write them to a file with --out."""
    con = ftw2e.connect()
    year = year or ftw2e.latest_year(con)
    box = parse_bbox(bbox)
    if out is None:
        output.emit(ftw2e.summarize(con, box, year), title=f"FTW {year} fields")
        return
    try:
        output.emit(ftw2e.export(con, box, year, out, limit))
    except (LookupError, ValueError) as e:
        fail(str(e))


@app.command
def view(
    *,
    bbox: BBoxArg,
    out: Annotated[Path, Parameter(help="PNG file to write.")],
    year: YearArg = None,
    width: Annotated[int, Parameter(help="Image width in pixels.")] = 1200,
    limit: Annotated[
        int, Parameter(help="Draw at most this many fields, largest first.")
    ] = 50_000,
):
    """Draw the fields in BBOX to a PNG, colored by model score."""
    from ftw_cli import render

    con = ftw2e.connect()
    year = year or ftw2e.latest_year(con)
    box = parse_bbox(bbox)
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
