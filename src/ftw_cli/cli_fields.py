"""``ftw compare`` and ``ftw stats``: work on field files you already have."""

from dataclasses import asdict
from pathlib import Path
from typing import Annotated

from cyclopts import Parameter, validators

from ftw_cli import match, output, vectors
from ftw_cli.cli_args import FieldsFile, FieldsOut, require
from ftw_cli.progress import spinner


def compare(
    reference: Annotated[FieldsFile, Parameter(help="Fields to score against.")],
    prediction: Annotated[FieldsFile, Parameter(help="Fields to score.")],
    *,
    iou: Annotated[float, Parameter(help="Minimum IoU for a match.")] = 0.5,
    out: Annotated[
        Path | None,
        Parameter(help="Write PREDICTION with match_iou and matched_id."),
    ] = None,
):
    """Score PREDICTION against REFERENCE with precision, recall, F1, and IoU.

    Fields match one to one by IoU, measured in the UTM zone at REFERENCE's center.
    """
    if out:
        vectors.check(out)
    con = vectors.connect()
    with spinner("Matching fields"):
        match.load(con, "a", f"select * from {vectors.source(reference)}")
        match.load(con, "b", f"select * from {vectors.source(prediction)}")
        match.project(con, "a", "b")
        result = asdict(match.compare(con, iou))
        if out:
            result |= {"path": str(out), "written": match.write_prediction(con, out)}
    output.emit(result, title="Comparison")


def stats(
    fields: FieldsFile,
    *,
    raster: Annotated[
        list[Path],
        Parameter(
            validator=validators.Path(exists=True, dir_okay=False),
            help="Raster to summarize; repeat for more.",
        ),
    ],
    out: FieldsOut,
    bands: Annotated[
        str | None, Parameter(help="Bands to summarize, such as 1,2. Default: all.")
    ] = None,
):
    """Add each field's mean, stdev, min, max, and count from raster bands.

    Pixels are weighted by the share of them inside the field (exactextract).
    """
    require("stats")
    from ftw_cli import zonal

    chosen = [int(b) for b in bands.split(",")] if bands else None
    with spinner("Summarizing rasters per field"):
        output.emit(zonal.add(fields, raster, out, chosen))
