"""``ftw data`` and ``ftw view``: the FTW Global Data 2nd Edition, read in place."""

from dataclasses import asdict
from pathlib import Path
from typing import Annotated

from cyclopts import App, Parameter

from ftw_cli import ftw2e, match, output, vectors
from ftw_cli.cli_args import (
    AreaArg,
    CrsArg,
    FieldsOut,
    MaxArea,
    MinArea,
    MinScore,
    YearArg,
    YearsArg,
    parse_area,
)
from ftw_cli.filters import Filters
from ftw_cli.progress import bar, spinner

data = App(name="data", help="Query the FTW Global Data 2nd Edition.")
IouArg = Annotated[float, Parameter(help="Minimum IoU for two fields to match.")]


@data.command
def info():
    """Files, fields, area, and download size per year."""
    output.emit(ftw2e.info(vectors.connect()), title="FTW Global Data 2nd Edition")


@data.command
def query(
    area: AreaArg,
    *,
    year: YearsArg = None,
    out: FieldsOut | None = None,
    limit: Annotated[
        int | None, Parameter(help="Write at most this many fields.")
    ] = None,
    crs: CrsArg = "4326",
    min_area: MinArea = None,
    max_area: MaxArea = None,
    min_score: MinScore = None,
    stats: Annotated[
        bool,
        Parameter(
            show_default=False, help="Add field-size percentiles and size classes, in ha."
        ),
    ] = False,
):
    """Summarize the fields in AREA per year, or write them with --out."""
    box = parse_area(area)
    filters = Filters(min_area, max_area, min_score)
    con = vectors.connect()
    years = ftw2e.parse_years(year, ftw2e.years(con))
    label = f"{years[0]}" if len(years) == 1 else f"{years[0]}-{years[-1]}"
    if out:
        with spinner(f"Writing FTW {label} fields to {out}"):
            output.emit(ftw2e.export(con, box, years, out, limit, crs, filters))
        return
    with bar(f"Reading FTW {label} fields") as progress:
        rows = ftw2e.summaries(box, years, progress, filters, stats)
    if len(rows) == 1:
        output.emit(rows[0], title=f"FTW {label} fields")
        return
    # One row per year; the full size statistics would make it too wide.
    keys = [
        "year",
        "fields",
        "area_ha",
        "mean_score",
        *(["median_ha"] if stats else []),
    ]
    output.emit([{k: r[k] for k in keys} for r in rows], title="FTW fields by year")
    years = [r["year"] for r in rows]
    output.trend("fields", [r["fields"] for r in rows], years)
    if stats:
        output.trend("median ha", [r["median_ha"] or 0 for r in rows], years)


@data.command
def diff(
    area: AreaArg,
    *,
    start: Annotated[int, Parameter(name="--from", help="Earlier year.")],
    end: Annotated[int, Parameter(name="--to", help="Later year.")],
    iou: IouArg = 0.5,
    out: Annotated[
        Path | None,
        Parameter(help="Write the later year's fields and the lost ones, with change."),
    ] = None,
):
    """Count fields kept, changed, split, merged, new, and lost between two years."""
    if out:
        vectors.check(out)
    box = parse_area(area)
    con = vectors.connect()
    with spinner(f"Matching FTW {start} and {end} fields"):
        match.load(con, "a", *ftw2e.select(con, box, start))
        match.load(con, "b", *ftw2e.select(con, box, end))
        match.project(con, "a", "b")
        rows = [asdict(c) for c in match.changes(con, iou)]
        written = match.write_changes(con, out, start, end) if out else None
    output.emit(rows, title=f"FTW fields, {start} to {end}")
    if out:
        output.stderr.print(f"[dim]wrote {written:,} fields to {out}[/dim]")


def view(
    area: AreaArg,
    *,
    out: Annotated[Path, Parameter(help="PNG to write.")],
    year: YearArg = None,
    width: Annotated[int, Parameter(help="Image width, px.")] = 1200,
    limit: Annotated[
        int, Parameter(help="Draw at most this many fields, largest first.")
    ] = 50_000,
    min_area: MinArea = None,
    max_area: MaxArea = None,
    min_score: MinScore = None,
):
    """Draw the fields in AREA to a PNG, colored by score."""
    from ftw_cli import render

    box = parse_area(area)
    con = vectors.connect()
    year = year or ftw2e.years(con)[-1]
    filters = Filters(min_area, max_area, min_score)
    with spinner(f"Drawing FTW {year} fields"):
        fields = ftw2e.fields(con, box, year, limit, filters)
        render.render(fields, box, width).save(out)
    output.emit({"path": str(out), "fields": len(fields), "year": year})
