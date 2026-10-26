"""Write sample outputs for the conformance job, which checks them with external validators.

Run as ``python tests/samples.py DIR``: writes ``DIR/scores.tif`` (a COG, as ``ftw predict``
writes it), ``DIR/fields.parquet`` (fiboa, as ``ftw polygonize`` writes it), and a minimal
STAC catalog around them so rashid can check the files against the Portolan data rules.
"""

import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import transform_bounds

from ftw_cli import cog, detect


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
        descriptions=detect.BANDS,
    )
    result = detect.polygonize(out / "scores.tif", out / "fields.parquet")
    assert result["fields"] == 2, result
    bbox = transform_bounds(
        profile["crs"], "EPSG:4326", 400_000, 4_639_000, 413_000, 4_650_000
    )
    catalog(out, list(bbox))


def catalog(out: Path, bbox: list[float]) -> None:
    """The least STAC rashid will read; its metadata findings are about this, not the files."""
    root = {"rel": "root", "href": "./catalog.json", "type": "application/json"}
    (out / "catalog.json").write_text(
        json.dumps(
            {
                "type": "Catalog",
                "stac_version": "1.1.0",
                "id": "samples",
                "description": "ftw-cli sample outputs",
                "links": [root, {"rel": "child", "href": "./collection.json"}],
            }
        )
    )
    (out / "collection.json").write_text(
        json.dumps(
            {
                "type": "Collection",
                "stac_version": "1.1.0",
                "id": "samples",
                "description": "ftw-cli sample outputs",
                "license": "CC-BY-4.0",
                "extent": {
                    "spatial": {"bbox": [bbox]},
                    "temporal": {"interval": [["2024-11-01T00:00:00Z", None]]},
                },
                "links": [root, {**root, "rel": "parent"}],
                "assets": {
                    "scores": {
                        "href": "./scores.tif",
                        "type": "image/tiff; application=geotiff; profile=cloud-optimized",
                        "roles": ["data"],
                    },
                    "fields": {
                        "href": "./fields.parquet",
                        "type": "application/vnd.apache.parquet",
                        "roles": ["data"],
                    },
                },
            }
        )
    )


if __name__ == "__main__":
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    main(out)
