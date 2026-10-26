"""``ftw detect``, ``predict``, ``polygonize``, ``imagery``, and ``models``."""

import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Literal

from cyclopts import App, Parameter, validators

from ftw_cli import models as registry
from ftw_cli import output, vectors
from ftw_cli.areas import BBox
from ftw_cli.cli_args import (
    AreaArg,
    CrsArg,
    FieldsOut,
    MaxArea,
    MinArea,
    parse_area,
    require,
)
from ftw_cli.progress import bar, spinner

imagery = App(name="imagery", help="Sentinel-2 scenes from the crop calendar.")
models = App(name="models", help="Released FTW models.")

ModelName = Annotated[registry.ModelName, Parameter(help="Model to run.")]
Host = Annotated[
    Literal["mspc", "earthsearch"],
    Parameter(help="STAC API: Planetary Computer or EarthSearch."),
]
Year = Annotated[int, Parameter(help="Growing season year.")]
BufferDays = Annotated[int, Parameter(help="Days either side of the season dates.")]
CloudMax = Annotated[float, Parameter(help="Maximum scene cloud cover, %.")]
ImageryTif = Annotated[
    Path,
    Parameter(
        validator=validators.Path(exists=True, dir_okay=False),
        help="Imagery from `ftw imagery download`.",
    ),
]
ScoresTif = Annotated[
    Path,
    Parameter(
        validator=validators.Path(exists=True, dir_okay=False),
        help="Probabilities from `ftw predict`.",
    ),
]


def detect(
    area: AreaArg,
    *,
    year: Year,
    out: FieldsOut,
    crs: CrsArg = "4326",
    min_area: MinArea = 500,
    max_area: MaxArea = None,
    model: ModelName = registry.DEFAULT_MODEL,
    keep: Annotated[
        Path | None,
        Parameter(help="Keep the imagery and probabilities here."),
    ] = None,
    host: Host = "mspc",
    buffer_days: BufferDays = 14,
    cloud_cover_max: CloudMax = 20,
):
    """Detect fields in AREA from Sentinel-2 scenes near YEAR's planting and harvest.

    The season comes from the crop calendar. For the 2nd Edition's fields (2017-2025),
    made with the quarterly model, use `ftw data query`.
    """
    vectors.check(out)
    if crs.lower() != "utm":
        vectors.crs(crs, utm="")
    box = parse_area(area)
    with tempfile.TemporaryDirectory() as tmp:
        work = keep or Path(tmp)
        scenes = _download(
            box, year, work / "imagery.tif", host, buffer_days, cloud_cover_max
        )
        _predict(work / "imagery.tif", work / "scores.tif", model, None, 4)
        fields = _polygonize(work / "scores.tif", out, 15, min_area, max_area, crs, box)
    kept = {"rasters": str(keep)} if keep else {}
    output.emit({**fields, "model": model, "scenes": scenes["scenes"], **kept})


def predict(
    input: ImageryTif,
    *,
    out: Annotated[Path, Parameter(help="Field and boundary probabilities COG.")],
    model: ModelName = registry.DEFAULT_MODEL,
    patch_size: Annotated[int | None, Parameter(help="Patch size, px.")] = None,
    batch_size: Annotated[int, Parameter(help="Patches per model call.")] = 4,
):
    """Predict field and boundary probabilities for INPUT."""
    output.emit(_predict(input, out, model, patch_size, batch_size))


def polygonize(
    input: ScoresTif,
    *,
    out: FieldsOut,
    crs: CrsArg = "4326",
    simplify: Annotated[float, Parameter(help="Simplification tolerance, m.")] = 15,
    min_area: MinArea = 500,
    max_area: MaxArea = None,
):
    """Split INPUT into fields with BoundaryVote."""
    output.emit(_polygonize(input, out, simplify, min_area, max_area, crs))


@imagery.command
def search(
    area: AreaArg,
    *,
    year: Year,
    host: Host = "mspc",
    buffer_days: BufferDays = 14,
    cloud_cover_max: CloudMax = 20,
):
    """The least cloudy scenes near the start and end of AREA's growing season."""
    scenes = _find(parse_area(area), year, host, buffer_days, cloud_cover_max)
    output.emit([asdict(s) for s in scenes], title="Scenes")


@imagery.command
def download(
    area: AreaArg,
    *,
    year: Year,
    out: Annotated[Path, Parameter(help="COG to write: RGBN per scene, 10 m.")],
    host: Host = "mspc",
    buffer_days: BufferDays = 14,
    cloud_cover_max: CloudMax = 20,
):
    """Download the two scenes over AREA as one COG."""
    output.emit(
        _download(parse_area(area), year, out, host, buffer_days, cloud_cover_max)
    )


@models.command(name="list")
def list_models():
    """Models, their licenses, and which is the default."""
    output.emit(
        [
            {
                "name": name,
                "default": name == registry.DEFAULT_MODEL,
                "cached": registry.cached(name) is not None,
                "license": model.license,
                "description": model.description,
            }
            for name, model in registry.MODELS.items()
        ],
        title="FTW models",
    )


def _find(box: BBox, year: int, host: str, buffer_days: int, cloud_cover_max: float):
    require("imagery")
    from ftw_cli import imagery as s2

    with spinner("Finding scenes on the crop calendar"):
        return s2.find(box, year, host, buffer_days, cloud_cover_max)


def _download(
    box: BBox, year: int, out: Path, host: str, buffer_days: int, cloud_cover_max: float
) -> dict:
    from ftw_cli import imagery as s2

    scenes = _find(box, year, host, buffer_days, cloud_cover_max)
    with bar("Reading Sentinel-2 bands") as progress:
        return s2.download(scenes, box, out, host, progress)


def _predict(
    input: Path,
    out: Path,
    model: registry.ModelName,
    patch_size: int | None,
    batch_size: int,
) -> dict:
    require("inference")
    from ftw_cli import inference

    if not registry.cached(model):
        with bar(f"Downloading {model}", unit="bytes") as progress:
            registry.fetch(model, progress)
    with bar(f"Running {model}") as progress:
        return inference.predict(input, out, model, patch_size, batch_size, progress)


def _polygonize(
    input: Path,
    out: Path,
    simplify: float,
    min_area: float | None,
    max_area: float | None,
    crs: str,
    area: BBox | None = None,
) -> dict:
    require("inference")
    from ftw_cli import inference

    with spinner("Splitting fields with BoundaryVote"):
        return inference.polygonize(input, out, simplify, min_area, max_area, crs, area)
