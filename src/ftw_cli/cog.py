"""Write rasters as Cloud Optimized GeoTIFFs."""

import tempfile
from pathlib import Path

import numpy as np
import rasterio
from rasterio.shutil import copy as copy_dataset  # ty: ignore[unresolved-import]

PROFILE_KEYS = ("crs", "transform", "width", "height", "nodata")


def write(
    out: Path,
    data: np.ndarray,
    profile: dict,
    tags: dict | None = None,
    descriptions: tuple[str, ...] | None = None,
) -> None:
    """Write ``data`` (bands, rows, cols) to ``out`` as a COG with averaged overviews.

    GDAL's COG driver only copies a dataset, so this writes a GeoTIFF to a temporary
    directory beside ``out`` first, on disk so a large tile isn't held in memory twice.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    base = {k: v for k, v in profile.items() if k in PROFILE_KEYS}
    with tempfile.TemporaryDirectory(dir=out.parent) as tmp:
        staged = Path(tmp) / out.name
        with rasterio.open(
            staged, "w", driver="GTiff", count=data.shape[0], dtype=data.dtype, **base
        ) as dst:
            dst.write(data)
            dst.update_tags(**(tags or {}))
            if descriptions:
                dst.descriptions = descriptions
        with rasterio.open(staged) as src:
            copy_dataset(
                src,
                out,
                driver="COG",
                compress="DEFLATE",
                predictor=2,
                blocksize=512,
                overview_resampling="average",
                statistics="YES",  # min/max/mean/stddev inside the file, as Portolan requires
                bigtiff="IF_SAFER",
            )
