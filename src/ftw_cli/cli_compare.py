"""``ftw compare`` and ``ftw data diff`` match field sets; ``ftw stats`` adds raster statistics."""

from dataclasses import asdict
from pathlib import Path
from typing import Annotated

from cyclopts import Parameter, validators

from ftw_cli import ftw2e, match, output, vectors
from ftw_cli.cli_args import AreaArg, parse_area, require
from ftw_cli.progress import spinner

FieldsFile = Annotated[
    Path, Parameter(validator=validators.Path(exists=True, dir_okay=False))
]
IouArg = Annotated[
    float, Parameter(help="Minimum intersection over union for a match.")
]


def compare(
    reference: Annotated[FieldsFile, Parameter(help="The fields to score against.")],
    prediction: Annotated[FieldsFile, Parameter(help="The fields to score.")],
    *,
    iou: IouArg = 0.5,
    out: Annotated[
        Path | None,
        Parameter(
            help="Write PREDICTION with match_iou and matched_id: .parquet, .gpkg, .geojson, or .fgb."
        ),
    ] = None,
):
    """Score PREDICTION's fields against REFERENCE's: matches, precision, recall, and IoU.

    Both are .parquet, .gpkg, .geojson, or .fgb in any CRS, measured in the UTM zone at
    REFERENCE's center.
    """
    if out:
        vectors.check(out)
    con = ftw2e.connect()
    with spinner("Matching fields"):
        match.load_file(con, "a", reference)
        match.load_file(con, "b", prediction)
        match.project(con, "a", "b")
        result = asdict(match.compare(con, iou))
        if out:
            result["path"] = str(out)
            result["written"] = match.write_prediction(con, out)
    output.emit(result, title="Comparison")


def diff(
    area: AreaArg,
    *,
    start: Annotated[int, Parameter(name="--from", help="The earlier year.")],
    end: Annotated[int, Parameter(name="--to", help="The later year.")],
    iou: IouArg = 0.5,
    out: Annotated[
        Path | None,
        Parameter(
            help="Write the later year's fields and the lost ones with year and change: "
            ".parquet, .gpkg, .geojson, or .fgb."
        ),
    ] = None,
):
    """How the fields in AREA changed between two years: kept, changed, split, merged, new, lost."""
    if out:
        vectors.check(out)
    box = parse_area(area)
    con = ftw2e.connect()
    with spinner(f"Matching FTW {start} and {end} fields"):
        match.load(con, "a", *ftw2e.select(con, box, start))
        match.load(con, "b", *ftw2e.select(con, box, end))
        match.project(con, "a", "b")
        rows = [asdict(c) for c in match.changes(con, iou)]
        written = match.write_changes(con, out, start, end) if out else None
    output.emit(rows, title=f"FTW fields, {start} to {end}")
    if out:
        output.stderr.print(f"[dim]wrote {written:,} fields to {out}[/dim]")


def stats(
    fields: Annotated[
        FieldsFile,
        Parameter(help="Fields to annotate: .parquet, .gpkg, .geojson, or .fgb."),
    ],
    *,
    raster: Annotated[
        list[Path],
        Parameter(
            validator=validators.Path(exists=True, dir_okay=False),
            negative="",
            help="A raster to summarize, such as a COG; repeat for more.",
        ),
    ],
    out: Annotated[
        Path,
        Parameter(help="Fields with statistics: .parquet, .gpkg, .geojson, or .fgb."),
    ],
    bands: Annotated[
        str | None, Parameter(help="Bands to summarize, such as 1,2. Defaults to all.")
    ] = None,
):
    """Add each field's mean, stdev, min, max, and pixel count from rasters."""
    require("stats")
    from ftw_cli import zonal

    chosen = [int(b) for b in bands.split(",")] if bands else None
    with spinner("Summarizing rasters per field"):
        output.emit(zonal.add(fields, raster, out, chosen))
