import numpy as np
import pytest

rasterio = pytest.importorskip("rasterio")

from ftw_cli import cog


def test_write_makes_a_cog_with_overviews_tags_and_descriptions(tmp_path):
    out = tmp_path / "scores.tif"
    data = np.random.default_rng(0).integers(0, 255, (2, 1100, 1300), dtype=np.uint8)
    profile = {
        "crs": "EPSG:32615",
        "transform": rasterio.transform.from_origin(400_000, 4_650_000, 10, 10),
        "width": 1300,
        "height": 1100,
    }

    cog.write(out, data, profile, tags={"FTW_MODEL": "m"}, descriptions=("a", "b"))

    with rasterio.open(out) as ds:
        assert ds.tags(ns="IMAGE_STRUCTURE")["LAYOUT"] == "COG"
        assert ds.overviews(1)
        assert ds.block_shapes[0] == (512, 512)
        assert ds.tags()["FTW_MODEL"] == "m"
        assert ds.descriptions == ("a", "b")
        assert (ds.read() == data).all()
    assert list(tmp_path.iterdir()) == [out]  # the temporary GeoTIFF is gone
