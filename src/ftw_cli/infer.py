"""Run FTW models on a stacked Sentinel-2 GeoTIFF and polygonize the result, without torch."""

import json
import math
from collections.abc import Callable
from importlib.metadata import version
from pathlib import Path

import duckdb
import numpy as np
import rasterio
from fbp import FTW_2E, run_windowed
from rasterio.features import shapes
from rasterio.windows import Window

from ftw_cli import __version__, cog, fiboa, models

# Score bands: field and boundary probability as uint8 (p * 255), like the 2nd Edition rasters.
BANDS = ("field", "boundary")


def run(
    input: Path,
    out: Path,
    model: str = models.DEFAULT_MODEL,
    patch_size: int | None = None,
    batch_size: int = 4,
    progress: Callable[[int, int | None], None] | None = None,
) -> dict:
    """Write field and boundary probabilities (uint8, p * 255) for ``input`` to a COG."""
    import onnxruntime as ort

    spec = models.MODELS[model]
    providers = [
        p
        for p in ("CUDAExecutionProvider", "CPUExecutionProvider")
        if p in ort.get_available_providers()
    ]
    session = ort.InferenceSession(str(models.fetch(model)), providers=providers)
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
            Window(col, height - size - row, size, size)
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
            for w, pred in zip(batch, predictions, strict=True):
                _paste(scores, pred, w, pad)
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
    min_size: float = 500.0,
    max_size: float | None = None,
) -> dict:
    """Split a ``run`` output into fields with ``fbp.FTW_2E`` and write fiboa GeoParquet.

    ``simplify`` is a tolerance in meters; ``min_size`` and ``max_size`` are areas in m².
    """
    with rasterio.open(input) as src:
        if not src.crs.is_projected:
            raise ValueError(
                f"{input} must be in a projected CRS (meters), not {src.crs}"
            )
        if src.count != 2:
            raise ValueError(
                f"{input} has {src.count} bands; expected field and boundary "
                "probabilities from `ftw infer run`"
            )
        field, boundary = src.read().astype(np.float32) / 255
        crs = src.crs.to_string()
        tags = src.tags()
        transform = src.transform
    prob = np.stack([np.clip(1 - field - boundary, 0, 1), field, boundary])
    del field, boundary
    instances = run_windowed(
        FTW_2E,
        prob.argmax(0).astype(np.uint8),
        prob,
        pixel_area_m2=abs(transform.a * transform.e),
    )
    del prob
    geoms = [
        json.dumps(g)
        for g, _ in shapes(instances, mask=instances > 0, transform=transform)
    ]

    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial; SET geometry_always_xy = true;")
    con.execute("create table shapes as select unnest(?) as g", [geoms])
    max_sql = f"and area <= {float(max_size)}" if max_size else ""
    query = f"""
        select concat('{_sql(input.stem)}-', row_number() over ()) as id,
               ST_Transform(ST_SimplifyPreserveTopology(geom, {float(simplify)}), '{crs}', 'EPSG:4326') as geometry,
               area::float as "metrics:area", ST_Perimeter(geom)::float as "metrics:perimeter"
        from (select ST_GeomFromGeoJSON(g) as geom, ST_Area(ST_GeomFromGeoJSON(g)) as area from shapes)
        where area >= {float(min_size)} {max_sql}
    """
    details = (
        f"{tags.get('FTW_MODEL', 'an FTW model')} on Sentinel-2 with ftw-cli {__version__}, "
        f"fields split with BoundaryVote (fbp.FTW_2E, ftw-fbp {version('ftw-fbp')})"
    )
    meta = fiboa.collection(
        "ftw-cli",
        **{"determination:method": "auto-imagery", "determination:details": details},
    )
    if when := _iso(tags.get("TIFFTAG_DATETIME")):
        meta["determination:datetime"] = when
    count = fiboa.copy(con, query, out, meta)
    return {"path": str(out), "fields": count, "crs": "EPSG:4326"}


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
