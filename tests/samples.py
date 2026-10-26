"""Write sample outputs for the conformance job, which checks them with external validators.

Run as ``python tests/samples.py DIR``: writes ``DIR/scores.tif`` (a COG, as ``ftw infer
run`` writes it) and ``DIR/fields.parquet`` (fiboa, as ``ftw infer polygonize`` writes it).
"""

import sys
from pathlib import Path

import numpy as np
import rasterio

from ftw_cli import cog, infer


def main(out: Path) -> None:
    field = np.zeros((1100, 1300), dtype=np.uint8)
    boundary = np.zeros_like(field)
    field[100:500, 100:1200] = 230
    boundary[100:500, 650] = 230  # one boundary line splits the block into two fields
    field[100:500, 650] = 20
    profile = {
        "crs": "EPSG:32615",
        "transform": rasterio.transform.from_origin(400_000, 4_650_000, 10, 10),
        "width": 1300,
        "height": 1100,
    }
    cog.write(
        out / "scores.tif",
        np.stack([field, boundary]),
        profile,
        tags={"FTW_MODEL": "sample", "TIFFTAG_DATETIME": "2024:11:01 17:03:49"},
        descriptions=infer.BANDS,
    )
    result = infer.polygonize(out / "scores.tif", out / "fields.parquet")
    assert result["fields"] == 2, result


if __name__ == "__main__":
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    main(out)
