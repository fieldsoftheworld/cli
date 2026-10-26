"""``ftw imagery``, ``ftw models``, and ``ftw infer``: from an area to field polygons."""

from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Literal

from cyclopts import App, Parameter, validators

from ftw_cli import models as registry
from ftw_cli import output
from ftw_cli.cli_args import AreaArg, parse_area, require
from ftw_cli.ftw2e import BBox
from ftw_cli.progress import bar, spinner

imagery = App(name="imagery", help="Find and download Sentinel-2 scenes for an area.")
models = App(name="models", help="Released FTW models the CLI can run.")
infer = App(name="infer", help="Run FTW models and split their output into fields.")

ModelName = Annotated[
    Literal[tuple(registry.MODELS)],  # type: ignore[valid-type]
    Parameter(help="Model to run; see `ftw models list`."),
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
InputTif = Annotated[
    Path, Parameter(validator=validators.Path(exists=True, dir_okay=False))
]


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


@infer.command
def run(
    input: InputTif,
    *,
    out: Annotated[Path, Parameter(help="COG of field and boundary probabilities.")],
    model: ModelName = registry.DEFAULT_MODEL,
    patch_size: Annotated[int | None, Parameter(help="Pixels per patch.")] = None,
    batch_size: Annotated[int, Parameter(help="Patches per model call.")] = 4,
):
    """Predict field and boundary probabilities for INPUT, from `ftw imagery download`."""
    output.emit(_run(input, out, model, patch_size, batch_size))


@infer.command
def polygonize(
    input: InputTif,
    *,
    out: Annotated[Path, Parameter(help="fiboa GeoParquet to write.")],
    simplify: Annotated[
        float, Parameter(help="Simplification tolerance in meters.")
    ] = 15,
    min_size: Annotated[float, Parameter(help="Drop smaller fields, in m².")] = 500,
    max_size: Annotated[
        float | None, Parameter(help="Drop larger fields, in m².")
    ] = None,
):
    """Split INPUT, from `ftw infer run`, into fields with BoundaryVote."""
    output.emit(_polygonize(input, out, simplify, min_size, max_size))


@infer.command(name="all")
def run_all(
    area: AreaArg,
    *,
    year: SceneYear,
    out_dir: Annotated[
        Path, Parameter(help="Gets imagery.tif, scores.tif, fields.parquet.")
    ],
    model: ModelName = registry.DEFAULT_MODEL,
    host: Host = "mspc",
    buffer_days: BufferDays = 14,
    cloud_cover_max: CloudMax = 20,
):
    """Download imagery for AREA, run MODEL on it, and split the result into fields."""
    box = parse_area(area)
    imagery_ = _download(
        box, year, out_dir / "imagery.tif", host, buffer_days, cloud_cover_max
    )
    scores = _run(out_dir / "imagery.tif", out_dir / "scores.tif", model, None, 4)
    fields = _polygonize(
        out_dir / "scores.tif", out_dir / "fields.parquet", 15, 500, None
    )
    output.emit(
        {**scores, **fields, "scenes": imagery_["scenes"], "path": str(out_dir)}
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


def _run(
    input: Path, out: Path, model: str, patch_size: int | None, batch_size: int
) -> dict:
    require("inference")
    from ftw_cli import infer as onnx

    if not registry.cached(model):
        with bar(f"Downloading {model}", unit="bytes") as progress:
            registry.fetch(model, progress)
    with bar(f"Running {model}") as progress:
        return onnx.run(input, out, model, patch_size, batch_size, progress)


def _polygonize(
    input: Path, out: Path, simplify: float, min_size: float, max_size: float | None
) -> dict:
    require("inference")
    from ftw_cli import infer as onnx

    with spinner("Splitting fields with BoundaryVote"):
        return onnx.polygonize(input, out, simplify, min_size, max_size)
