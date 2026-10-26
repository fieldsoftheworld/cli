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
        Parameter(
            help="Keep the rasters in this directory; with ftw-q, a rerun reuses their scores."
        ),
    ] = None,
    host: Host = "mspc",
    buffer_days: BufferDays = 14,
    cloud_cover_max: CloudMax = 20,
):
    """Find fields in AREA: get imagery for YEAR, run MODEL, split into polygons.

    ftw-q, the default, runs on the year's quarterly mosaics tile by tile as the 2nd Edition
    was made; the PRUE models run on two scenes picked from the crop calendar.
    """
    vectors.check(out)
    if crs.lower() != "utm":
        vectors.crs(crs, utm="")
    box = parse_area(area)
    if registry.MODELS[model].imagery == "mosaics":
        output.emit(_detect_tiles(box, year, out, model, crs, keep))
        return
    with tempfile.TemporaryDirectory() as tmp:
        work = keep or Path(tmp)
        scenes = _download(
            box, year, work / "imagery.tif", host, buffer_days, cloud_cover_max
        )
        _predict(work / "imagery.tif", work / "scores.tif", model, None, 4)
        fields = _polygonize(work / "scores.tif", out, 15, 500, None, crs, box)
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


def _detect_tiles(
    box: BBox,
    year: int,
    out: Path,
    model: registry.ModelName,
    crs: str,
    keep: Path | None,
) -> dict:
    require("inference")
    from ftw_cli import detect as onnx
    from ftw_cli import ftw2e, mosaics, quarterly

    con = ftw2e.connect()
    with spinner("Finding quarterly mosaic tiles"):
        parts = [
            p
            for t in mosaics.tiles(con, box, year)
            if (p := mosaics.locate(t, year, box))
        ]
    if not parts:
        raise LookupError(f"no quarterly mosaic tile overlaps {box}")
    if not registry.cached(model):
        with bar(f"Downloading {model}", unit="bytes") as progress:
            registry.fetch(model, progress)
    session = onnx.open_session(model)
    # utm writes the whole output in one zone: the first tile's.
    target = vectors.crs(crs, utm=parts[0].crs)
    for i, part in enumerate(parts, 1):
        label = f"{part.tile.key} ({i}/{len(parts)})"
        path = keep / f"{part.tile.key}.tif" if keep else None
        scores = mosaics.kept(path, part, model) if path else None
        if scores is None:
            with bar(f"Reading {label}") as progress:
                stack = mosaics.read(part, progress)
            with bar(f"Running {model} on {label}") as progress:
                scores = quarterly.predict(stack, session, progress=progress)
            if path:
                mosaics.save(path, scores, part, model)
        inside = f"ST_Intersects(ST_Transform(geom, '{part.crs}', 'EPSG:4326'), {box.envelope()})"
        with spinner(f"Splitting fields on {label}"):
            onnx.split_into(
                con,
                scores,
                part.scores_transform,
                part.crs,
                target,
                part.tile.key,
                keep=f"{mosaics.owned(part)} and {inside}",
            )
    meta = onnx.collection(model, f"Sentinel-2 quarterly mosaics {year}")
    meta["determination:datetime"] = f"{year}-01-01T00:00:00Z"
    count = vectors.write(con, "select * from fields", out, meta, crs=target)
    tiles = [p.tile.key for p in parts]
    kept = {"rasters": str(keep)} if keep else {}
    return {
        "path": str(out),
        "fields": count,
        "crs": target,
        "model": model,
        "tiles": tiles,
        **kept,
    }


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
    area: BBox | None = None,
) -> dict:
    require("inference")
    from ftw_cli import detect as onnx

    with spinner("Splitting fields with BoundaryVote"):
        return onnx.polygonize(input, out, simplify, min_size, max_size, crs, area)
