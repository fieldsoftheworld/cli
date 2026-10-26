"""Write rasters as Cloud Optimized GeoTIFFs."""

import os
import tempfile
from pathlib import Path

import numpy as np
import rasterio
from rasterio.shutil import copy as copy_dataset  # ty: ignore[unresolved-import]


def write(
    out: Path,
    data: np.ndarray,
    profile: dict,
    tags: dict | None = None,
    descriptions: tuple[str, ...] | None = None,
) -> None:
    """Write ``data`` (bands, rows, cols) to ``out`` as a COG with averaged overviews.

    GDAL's COG driver only copies an existing dataset, so this writes a plain GeoTIFF
    beside ``out`` first, on disk rather than in memory so a large tile isn't held twice.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".tif", dir=out.parent)
    os.close(fd)
    try:
        base = {
            k: v
            for k, v in profile.items()
            if k in ("crs", "transform", "width", "height", "nodata")
        }
        with rasterio.open(
            tmp,
            "w",
            driver="GTiff",
            count=data.shape[0],
            dtype=data.dtype,
            **base,
        ) as dst:
            dst.write(data)
            if tags:
                dst.update_tags(**tags)
            if descriptions:
                dst.descriptions = descriptions
        with rasterio.open(tmp) as src:
            copy_dataset(
                src,
                out,
                driver="COG",
                compress="DEFLATE",
                predictor=2,
                blocksize=512,
                overview_resampling="average",
                bigtiff="IF_SAFER",
            )
    finally:
        os.remove(tmp)
