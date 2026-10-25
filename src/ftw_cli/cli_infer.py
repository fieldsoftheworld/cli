"""``ftw imagery``, ``ftw models``, and ``ftw infer``: from an area to field polygons."""

from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Literal

from cyclopts import App, Parameter, validators

from ftw_cli import models as registry
from ftw_cli import output
from ftw_cli.cli_args import BBoxArg, fail, need, parse_bbox

imagery = App(
    name="imagery",
    help="Find and download the planting and harvest Sentinel-2 scenes for an area.",
)
models = App(name="models", help="Released FTW models the CLI can run.")
infer = App(
    name="infer", help="Run FTW models and turn their output into field polygons."
)

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
    int, Parameter(help="Search this many days either side of the crop calendar date.")
]
CloudMax = Annotated[float, Parameter(help="Maximum scene cloud cover, in percent.")]


@imagery.command
def search(
    *,
    bbox: BBoxArg,
    year: SceneYear,
    host: Host = "mspc",
    buffer_days: BufferDays = 14,
    cloud_cover_max: CloudMax = 20,
):
    """The least cloudy scenes near the start and end of the growing season over BBOX."""
    output.emit(_search(bbox, year, host, buffer_days, cloud_cover_max), title="Scenes")


@imagery.command
def download(
    *,
    bbox: BBoxArg,
    year: SceneYear,
    out: Annotated[
        Path, Parameter(help="GeoTIFF to write: 8 bands, RGBN of each scene, 10 m.")
    ],
    host: Host = "mspc",
    buffer_days: BufferDays = 14,
    cloud_cover_max: CloudMax = 20,
):
    """Search for the two scenes, then stack them over BBOX into one GeoTIFF."""
    output.emit(_download(bbox, year, out, host, buffer_days, cloud_cover_max))


@models.command(name="list")
def list_models():
    """The models, with license and which one is the default."""
    output.emit(
        [
            {
                "name": n,
                "default": n == registry.DEFAULT_MODEL,
                "license": m.license,
                "description": m.description,
            }
            for n, m in registry.MODELS.items()
        ],
        title="FTW models",
    )


@models.command
def show(name: ModelName):
    """Everything about one model, including its pinned download URL."""
    model = registry.MODELS[name]
    cached = registry.cache_dir() / model.url.rsplit("/", 1)[-1]
    output.emit(
        {
            "name": name,
            **asdict(model),
            "cached": str(cached) if cached.exists() else None,
        }
    )


@infer.command
def run(
    input: Annotated[
        Path,
        Parameter(
            validator=validators.Path(exists=True, dir_okay=False),
            help="Stacked GeoTIFF, as written by `ftw imagery download`.",
        ),
    ],
    *,
    out: Annotated[
        Path, Parameter(help="GeoTIFF of classes: 0 background, 1 field, 2 boundary.")
    ],
    model: ModelName = registry.DEFAULT_MODEL,
    patch_size: Annotated[
        int | None,
        Parameter(help="Pixels per patch. Defaults to 1024 or less for small inputs."),
    ] = None,
    batch_size: Annotated[int, Parameter(help="Patches per model call.")] = 4,
):
    """Classify every pixel of INPUT as background, field, or field boundary."""
    output.emit(_run(input, out, model, patch_size, batch_size))


@infer.command
def polygonize(
    input: Annotated[
        Path,
        Parameter(
            validator=validators.Path(exists=True, dir_okay=False),
            help="Classes GeoTIFF from `ftw infer run`.",
        ),
    ],
    *,
    out: Annotated[Path, Parameter(help="GeoParquet to write, in EPSG:4326.")],
    simplify: Annotated[
        float, Parameter(help="Simplification tolerance in meters; 0 disables it.")
    ] = 15,
    min_size: Annotated[
        float, Parameter(help="Drop fields smaller than this, in m².")
    ] = 500,
    max_size: Annotated[
        float | None, Parameter(help="Drop fields larger than this, in m².")
    ] = None,
):
    """Turn the field class of INPUT into polygons with area and perimeter."""
    output.emit(_polygonize(input, out, simplify, min_size, max_size))


@infer.command(name="all")
def run_all(
    *,
    bbox: BBoxArg,
    year: SceneYear,
    out_dir: Annotated[
        Path,
        Parameter(help="Directory for imagery.tif, classes.tif, and fields.parquet."),
    ],
    model: ModelName = registry.DEFAULT_MODEL,
    host: Host = "mspc",
    buffer_days: BufferDays = 14,
    cloud_cover_max: CloudMax = 20,
):
    """Download imagery for BBOX, run MODEL on it, and polygonize the fields."""
    out_dir.mkdir(parents=True, exist_ok=True)
    scenes = _download(
        bbox, year, out_dir / "imagery.tif", host, buffer_days, cloud_cover_max
    )
    classes = _run(out_dir / "imagery.tif", out_dir / "classes.tif", model, None, 4)
    fields = _polygonize(
        out_dir / "classes.tif", out_dir / "fields.parquet", 15, 500, None
    )
    output.emit(
        {"scenes": scenes["scenes"], "model": model, **classes, **fields}
        | {"path": str(out_dir)}
    )


def _search(
    bbox: str, year: int, host: str, buffer_days: int, cloud_cover_max: float
) -> list[dict]:
    try:
        from ftw_cli import imagery as s2
    except ImportError as e:
        need("imagery", e)
    box = parse_bbox(bbox)
    try:
        start, end = s2.season(box, year)
        return [
            {
                "window": window,
                "target_date": str(when),
                **asdict(s2.search(box, when, host, buffer_days, cloud_cover_max)),
            }
            for window, when in (("a", start), ("b", end))
        ]
    except (LookupError, ValueError, OSError) as e:
        fail(str(e))


def _download(
    bbox: str, year: int, out: Path, host: str, buffer_days: int, cloud_cover_max: float
) -> dict:
    from ftw_cli import imagery as s2

    found = _search(bbox, year, host, buffer_days, cloud_cover_max)
    scenes = [
        s2.Scene(**{k: v for k, v in s.items() if k in s2.Scene.__dataclass_fields__})
        for s in found
    ]
    return s2.download(scenes, parse_bbox(bbox), out, host)


def _run(
    input: Path, out: Path, model: str, patch_size: int | None, batch_size: int
) -> dict:
    try:
        from ftw_cli import infer as onnx
    except ImportError as e:
        need("inference", e)
    try:
        return onnx.run(input, out, model, patch_size, batch_size)
    except (ValueError, OSError) as e:
        fail(str(e))


def _polygonize(
    input: Path, out: Path, simplify: float, min_size: float, max_size: float | None
) -> dict:
    try:
        from ftw_cli import infer as onnx
    except ImportError as e:
        need("inference", e)
    try:
        return onnx.polygonize(input, out, simplify, min_size, max_size)
    except (ValueError, OSError) as e:
        fail(str(e))
