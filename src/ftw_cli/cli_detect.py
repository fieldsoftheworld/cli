"""``ftw detect``, ``predict``, and ``polygonize``, plus ``imagery`` and ``models``."""

import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Literal

from cyclopts import App, Parameter, validators

from ftw_cli import models as registry
from ftw_cli import output, vectors
from ftw_cli.cli_args import AreaArg, CrsArg, parse_area, require
from ftw_cli.ftw2e import BBox
from ftw_cli.progress import bar, spinner

imagery = App(name="imagery", help="Find and download Sentinel-2 scenes for an area.")
models = App(name="models", help="Released FTW models the CLI can run.")

ModelName = Annotated[
    registry.ModelName, Parameter(help="Model to run; see `ftw models list`.")
]
Host = Annotated[
    Literal["mspc", "earthsearch"],
    Parameter(
        help="STAC host: Microsoft Planetary Computer or Element 84 EarthSearch."
    ),
]
SceneYear = Annotated[int, Parameter(help="Growing season year.")]
BufferDays = Annotated[
    int, Parameter(help="Days either side of the crop calendar date.")
]
CloudMax = Annotated[float, Parameter(help="Maximum scene cloud cover, in percent.")]
ImageryTif = Annotated[
    Path,
    Parameter(
        validator=validators.Path(exists=True, dir_okay=False),
        help="Imagery COG from `ftw imagery download`.",
    ),
]
ScoresTif = Annotated[
    Path,
    Parameter(
        validator=validators.Path(exists=True, dir_okay=False),
        help="Probabilities COG from `ftw predict`.",
    ),
]
FieldsOut = Annotated[
    Path, Parameter(help="Fields to write: .parquet (fiboa), .gpkg, .geojson, or .fgb.")
]
Simplify = Annotated[float, Parameter(help="Simplification tolerance in meters.")]
MinSize = Annotated[float, Parameter(help="Drop smaller fields, in m².")]


def detect(
    area: AreaArg,
    *,
    year: SceneYear,
    out: FieldsOut,
    crs: CrsArg = "4326",
    model: ModelName = registry.DEFAULT_MODEL,
    keep: Annotated[
        Path | None,
        Parameter(help="Keep imagery.tif and scores.tif in this directory."),
    ] = None,
    host: Host = "mspc",
    buffer_days: BufferDays = 14,
    cloud_cover_max: CloudMax = 20,
):
    """Find fields in AREA: download imagery for YEAR, run MODEL, split into polygons."""
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
        fields = _polygonize(work / "scores.tif", out, 15, 500, None, crs)
    kept = {"rasters": str(keep)} if keep else {}
    output.emit({**fields, "model": model, "scenes": scenes["scenes"], **kept})


def predict(
    input: ImageryTif,
    *,
    out: Annotated[Path, Parameter(help="COG of field and boundary probabilities.")],
    model: ModelName = registry.DEFAULT_MODEL,
    patch_size: Annotated[int | None, Parameter(help="Pixels per patch.")] = None,
    batch_size: Annotated[int, Parameter(help="Patches per model call.")] = 4,
):
    """Predict field and boundary probabilities for INPUT."""
    output.emit(_predict(input, out, model, patch_size, batch_size))


def polygonize(
    input: ScoresTif,
    *,
    out: FieldsOut,
    crs: CrsArg = "4326",
    simplify: Simplify = 15,
    min_size: MinSize = 500,
    max_size: Annotated[
        float | None, Parameter(help="Drop larger fields, in m².")
    ] = None,
):
    """Split INPUT into fields with BoundaryVote."""
    output.emit(_polygonize(input, out, simplify, min_size, max_size, crs))


@imagery.command
def search(
    area: AreaArg,
    *,
    year: SceneYear,
    host: Host = "mspc",
    buffer_days: BufferDays = 14,
    cloud_cover_max: CloudMax = 20,
):
    """The least cloudy scenes near the start and end of the growing season over AREA."""
    scenes = _find(parse_area(area), year, host, buffer_days, cloud_cover_max)
    output.emit([asdict(s) for s in scenes], title="Scenes")


@imagery.command
def download(
    area: AreaArg,
    *,
    year: SceneYear,
    out: Annotated[Path, Parameter(help="COG to write: RGBN of each scene, 10 m.")],
    host: Host = "mspc",
    buffer_days: BufferDays = 14,
    cloud_cover_max: CloudMax = 20,
):
    """Find the two scenes, then stack them over AREA into one COG."""
    output.emit(
        _download(parse_area(area), year, out, host, buffer_days, cloud_cover_max)
    )


@models.command(name="list")
def list_models():
    """The models, their licenses, and which is the default."""
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
    from ftw_cli import detect as onnx

    if not registry.cached(model):
        with bar(f"Downloading {model}", unit="bytes") as progress:
            registry.fetch(model, progress)
    with bar(f"Running {model}") as progress:
        return onnx.predict(input, out, model, patch_size, batch_size, progress)


def _polygonize(
    input: Path,
    out: Path,
    simplify: float,
    min_size: float,
    max_size: float | None,
    crs: str,
) -> dict:
    require("inference")
    from ftw_cli import detect as onnx

    with spinner("Splitting fields with BoundaryVote"):
        return onnx.polygonize(input, out, simplify, min_size, max_size, crs)
