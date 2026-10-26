"""``ftw compare``: score one set of fields against another."""

from dataclasses import asdict
from pathlib import Path
from typing import Annotated

from cyclopts import Parameter, validators

from ftw_cli import ftw2e, match, output, vectors
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
