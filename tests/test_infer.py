import json

import numpy as np
import pyarrow.parquet as pq
import pytest

rasterio = pytest.importorskip("rasterio")

from ftw_cli import infer


def test_patch_grid_is_centered_like_torchgeo():
    # 1118 px with 512 px patches and stride 448: ceil(606 / 448) + 1 = 3 patches spanning
    # 1408 px, so the 290 px overhang is split evenly between both ends.
    assert infer._starts(1118, 512, 448) == [-145, 303, 751]


def test_upsample_aligns_corners():
    # kornia's Resize (used in training) keeps the corner pixels and spaces samples evenly.
    image = np.array([[[0.0, 3.0]]], dtype=np.float32)
    assert np.allclose(infer._upsample(image, 2)[0, 0], [0.0, 1.0, 2.0, 3.0])


def test_polygonize_measures_meters_and_writes_wgs84(tmp_path):
    classes = np.zeros((40, 40), dtype=np.uint8)
    classes[10:30, 10:30] = infer.FIELD  # 20 x 20 pixels at 10 m = 40,000 m²
    path = tmp_path / "classes.tif"
    transform = rasterio.transform.from_origin(400_000, 4_650_000, 10, 10)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=40,
        height=40,
        count=1,
        dtype="uint8",
        crs="EPSG:32615",
        transform=transform,
    ) as dst:
        dst.write(classes, 1)

    result = infer.polygonize(path, tmp_path / "fields.parquet", simplify=0, min_size=0)

    assert result["fields"] == 1
    table = pq.read_table(tmp_path / "fields.parquet")
    assert table.column("metrics:area")[0].as_py() == pytest.approx(40_000)
    crs = json.loads(table.schema.metadata[b"geo"])["columns"]["geometry"]["crs"]
    assert crs["id"] == {"authority": "EPSG", "code": 4326}
