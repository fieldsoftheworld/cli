"""Detect fields: predict field and boundary probabilities with ONNX, then split them into polygons."""

import json
import math
from collections.abc import Callable
from importlib.metadata import version
from pathlib import Path

import duckdb
import numpy as np
import pyarrow as pa
import rasterio
from fbp import FTW_2E, run_windowed
from rasterio.features import shapes
from rasterio.windows import Window

from ftw_cli import __version__, cog, fiboa, models, vectors
from ftw_cli.filters import Filters
from ftw_cli.ftw2e import BBox

# Score bands: field and boundary probability as uint8 (p * 255), like the 2nd Edition rasters.
BANDS = ("field", "boundary")
# Declared the way the 2nd Edition declares its own score column.
SCORE_SCHEMA = {
    "$schema": fiboa.SDL,
    "properties": {
        "score": {
            "type": "uint8",
            "description": "Mean model field probability inside the field, x 100 rounded (0-100)",
        },
        "field_prob": {
            "type": "float",
            "description": "Mean model field probability (0-1)",
        },
        "boundary_prob": {
            "type": "float",
            "description": "Mean model boundary probability (0-1)",
        },
    },
}


def predict(
    input: Path,
    out: Path,
    model: models.ModelName = models.DEFAULT_MODEL,
    patch_size: int | None = None,
    batch_size: int = 4,
    progress: Callable[[int, int | None], None] | None = None,
) -> dict:
    """Write field and boundary probabilities (uint8, p * 255) for ``input`` to a COG."""
    spec = models.MODELS[model]
    if spec.imagery != "scenes":
        raise ValueError(
            f"{model} runs on quarterly mosaics; use `ftw detect --model {model}`"
        )
    session = open_session(model)
    input_name = session.get_inputs()[0].name

    with rasterio.open(input) as src:
        height, width = src.height, src.width
        size = patch_size or next(
            (s for s in (1024, 512, 256, 128) if s <= min(height, width)), None
        )
        if size is None or size > min(height, width):
            raise ValueError(
                f"{input} is {width}x{height}; it needs at least 128 pixels per side"
            )
        if size * spec.resize_factor % 32:
            raise ValueError(
                f"patch size {size} times resize factor {spec.resize_factor} must be a multiple of 32"
            )
        pad = math.ceil(min(1024, size) / 16)
        stride = size - 2 * pad
        # Rows go south to north, like torchgeo's sampler, since later patches overwrite overlaps.
        windows = [
            Window(col, height - size - row, size, size)  # ty: ignore[too-many-positional-arguments]
            for row in _starts(height, size, stride)
            for col in _starts(width, size, stride)
        ]
        scores = np.zeros((2, height, width), dtype=np.uint8)
        for i in range(0, len(windows), batch_size):
            batch = windows[i : i + batch_size]
            images = np.stack(
                [
                    _prepare(src.read(window=w, boundless=True, fill_value=0), spec)
                    for w in batch
                ]
            )
            logits = np.asarray(session.run(None, {input_name: images})[0])
            f = spec.resize_factor
            probs = _softmax(logits[:, :, ::f, ::f])[:, 1:]
            predictions = np.rint(probs * 255).astype(np.uint8)
            for j, w in enumerate(batch):
                _paste(scores, predictions[j], w, pad)
            if progress:
                progress(i + len(batch), len(windows))
        profile = src.profile | {"nodata": None}
        tags = src.tags()

    cog.write(
        out,
        scores,
        profile,
        tags={
            "FTW_MODEL": model,
            **{k: v for k, v in tags.items() if k == "TIFFTAG_DATETIME"},
        },
        descriptions=BANDS,
    )
    return {"path": str(out), "model": model, "patches": len(windows)}


def polygonize(
    input: Path,
    out: Path,
    simplify: float = 15.0,
    min_area: float | None = 500.0,
    max_area: float | None = None,
    crs: str = "4326",
    area: BBox | None = None,
) -> dict:
    """Split ``predict``'s probabilities into fields with ``fbp.FTW_2E`` and write them to ``out``.

    ``simplify`` is a tolerance in meters; ``min_area`` and ``max_area`` are in m².
    ``crs`` is an EPSG code, or ``utm`` for the raster's own CRS. With an ``area`` that has a
    polygon, only fields that intersect it are kept.
    """
    vectors.check(out)
    with rasterio.open(input) as src:
        if not src.crs.is_projected:
            raise ValueError(
                f"{input} must be in a projected CRS (meters), not {src.crs}"
            )
        if src.count != 2:
            raise ValueError(
                f"{input} has {src.count} bands; expected field and boundary "
                "probabilities from `ftw predict`"
            )
        scores = src.read()
        native = src.crs.to_string()
        tags = src.tags()
        transform = src.transform
    target = vectors.crs(crs, utm=native)
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial; SET geometry_always_xy = true;")
    inside = "true"
    if area and area.shape:
        inside = f"ST_Intersects(ST_Transform(geom, '{native}', 'EPSG:4326'), {area.envelope()})"
    split_into(
        con,
        scores,
        transform,
        native,
        target,
        input.stem,
        simplify,
        min_area,
        max_area,
        keep=inside,
    )
    meta = collection(tags.get("FTW_MODEL", "an FTW model"), "Sentinel-2")
    if when := _iso(tags.get("TIFFTAG_DATETIME")):
        meta["determination:datetime"] = when
    count = vectors.write(con, "select * from fields", out, meta, crs=target)
    return {"path": str(out), "fields": count, "crs": target}


def open_session(model: models.ModelName):
    """An onnxruntime session for ``model``, on a GPU when onnxruntime has one."""
    import onnxruntime as ort

    providers = [
        p
        for p in ("CUDAExecutionProvider", "CPUExecutionProvider")
        if p in ort.get_available_providers()
    ]
    return ort.InferenceSession(str(models.fetch(model)), providers=providers)


def split_into(
    con: duckdb.DuckDBPyConnection,
    scores: np.ndarray,
    transform,
    native: str,
    target: str,
    prefix: str,
    simplify: float = 15.0,
    min_area: float | None = 500.0,
    max_area: float | None = None,
    keep: str = "true",
) -> None:
    """Split uint8 field and boundary ``scores`` with ``fbp.FTW_2E`` into table ``fields``.

    ``keep`` is an SQL predicate over ``geom`` in the ``native`` CRS; ids are ``prefix-n``.
    A field whose pixels form several pieces keeps the largest, so each is one Polygon.
    """
    field, boundary = scores.astype(np.float32) / 255
    prob = np.stack([np.clip(1 - field - boundary, 0, 1), field, boundary])
    del field, boundary
    instances = run_windowed(
        FTW_2E,
        prob.argmax(0).astype(np.uint8),
        prob,
        pixel_area_m2=abs(transform.a * transform.e),
    )
    del prob
    pieces = list(shapes(instances, mask=instances > 0, transform=transform))
    con.execute(
        "create or replace temp table shapes as "
        "select unnest(?::varchar[]) as g, unnest(?::bigint[]) as label",
        [[json.dumps(g) for g, _ in pieces], [int(v) for _, v in pieces]],
    )
    del pieces
    con.register("probs", _mean_probs(instances, scores))
    con.execute(
        'create table if not exists fields (id varchar, geometry geometry, "metrics:area" float, '
        '"metrics:perimeter" float, score utinyint, field_prob float, boundary_prob float)'
    )
    con.execute(
        f"""
        insert into fields
        select concat('{_sql(prefix)}-', row_number() over ()),
               ST_Transform(ST_SimplifyPreserveTopology(geom, {float(simplify)}), '{native}', '{target}'),
               area::float, ST_Perimeter(geom)::float,
               least(100, greatest(0, round(field_prob * 100)))::utinyint,
               field_prob::float, boundary_prob::float
        from (
            select geom, ST_Area(geom) as area, label
            from (select ST_GeomFromGeoJSON(g) as geom, label from shapes)
            qualify row_number() over (partition by label order by area desc) = 1
        ) join probs using (label)
        where {Filters(min_area, max_area).sql(area="area")} and {keep}
        """
    )


def _mean_probs(
    instances: np.ndarray, scores: np.ndarray, rows: int = 1024
) -> pa.Table:
    """Each field's mean field and boundary probability over its pixels, a block at a time."""
    n = int(instances.max()) + 1
    sums = np.zeros((3, n))
    for r in range(0, instances.shape[0], rows):
        labels = instances[r : r + rows].ravel()
        sums[0] += np.bincount(labels, minlength=n)
        for i, band in enumerate(scores[:, r : r + rows], 1):
            sums[i] += np.bincount(labels, band.ravel(), n)
    field, boundary = sums[1:] / np.maximum(sums[0], 1) / 255
    return pa.table(
        {"label": np.arange(n), "field_prob": field, "boundary_prob": boundary}
    )


def collection(model: str, imagery: str) -> dict:
    details = (
        f"{model} on {imagery} with ftw-cli {__version__}, fields split with BoundaryVote "
        f"(fbp.FTW_2E, ftw-fbp {version('ftw-fbp')})"
    )
    return fiboa.collection(
        "ftw-cli",
        **{
            "determination:method": "auto-imagery",
            "determination:details": details,
            "schemas:custom": SCORE_SCHEMA,
        },
    )


def _sql(text: str) -> str:
    return text.replace("'", "''")


def _starts(length: int, size: int, stride: int) -> list[int]:
    """Patch offsets from the low edge, matching torchgeo's ``GriddedPatchSampler``.

    The grid has ``ceil((length - size) / stride) + 1`` patches and is centered on the
    image, so its overhang is split between both ends.
    """
    count = math.ceil((length - size) / stride) + 1
    low = round(-(stride * (count - 1) + size - length) / 2)
    return [low + k * stride for k in range(count)]


def _prepare(pixels: np.ndarray, spec: models.Model) -> np.ndarray:
    image = np.nan_to_num(pixels.astype(np.float32) / spec.normalization)
    return image if spec.resize_factor == 1 else _upsample(image, spec.resize_factor)


def _softmax(logits: np.ndarray) -> np.ndarray:
    """Class probabilities from ``(batch, class, h, w)`` logits."""
    e = np.exp(logits - logits.max(axis=1, keepdims=True))
    return e / e.sum(axis=1, keepdims=True)


def _upsample(image: np.ndarray, factor: int) -> np.ndarray:
    """Bilinear upsampling with aligned corners, matching kornia's ``Resize`` used in training."""

    def axis(n: int) -> tuple[np.ndarray, np.ndarray]:
        src = np.arange(n * factor) * (n - 1) / (n * factor - 1)
        i0 = np.minimum(np.floor(src).astype(int), n - 2)
        return i0, (src - i0).astype(np.float32)

    (r0, rt), (c0, ct) = axis(image.shape[1]), axis(image.shape[2])
    rows = image[:, r0] * (1 - rt)[None, :, None] + image[:, r0 + 1] * rt[None, :, None]
    return rows[:, :, c0] * (1 - ct) + rows[:, :, c0 + 1] * ct


def _paste(scores: np.ndarray, pred: np.ndarray, w: Window, pad: int) -> None:
    """Copy ``pred`` into ``scores``, dropping ``pad`` pixels on sides not at the image edge.

    Patches can hang past the image; their outside part is clipped.
    """
    height, width = scores.shape[-2:]
    top, left = int(w.row_off), int(w.col_off)
    bottom, right = top + w.height, left + w.width
    pad_top = 0 if top <= 0 else pad
    pad_left = 0 if left <= 0 else pad
    inner_top, inner_left = top + pad_top, left + pad_left
    inner_bottom = bottom - (0 if bottom >= height else pad)
    inner_right = right - (0 if right >= width else pad)
    dst_top, dst_left = max(inner_top, 0), max(inner_left, 0)
    dst_bottom, dst_right = min(inner_bottom, height), min(inner_right, width)
    if dst_bottom <= dst_top or dst_right <= dst_left:
        return
    src_top, src_left = pad_top + dst_top - inner_top, pad_left + dst_left - inner_left
    scores[..., dst_top:dst_bottom, dst_left:dst_right] = pred[
        ...,
        src_top : src_top + dst_bottom - dst_top,
        src_left : src_left + dst_right - dst_left,
    ]


def _iso(stamp: str | None) -> str | None:
    """``2024:11:01 17:03:49`` (TIFFTAG_DATETIME) to ``2024-11-01T17:03:49Z``."""
    if stamp and len(stamp) >= 19:
        return f"{stamp[:4]}-{stamp[5:7]}-{stamp[8:10]}T{stamp[11:19]}Z"
    return None
