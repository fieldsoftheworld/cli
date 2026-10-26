import json

import numpy as np
import pyarrow.parquet as pq
import pytest

rasterio = pytest.importorskip("rasterio")

from ftw_cli import detect


def test_patch_grid_is_centered_like_torchgeo():
    # 1118 px with 512 px patches and stride 448: ceil(606 / 448) + 1 = 3 patches spanning
    # 1408 px, so the 290 px overhang is split evenly between both ends.
    assert detect._starts(1118, 512, 448) == [-145, 303, 751]


def test_upsample_aligns_corners():
    # kornia's Resize (used in training) keeps the corner pixels and spaces samples evenly.
    image = np.array([[[0.0, 3.0]]], dtype=np.float32)
    assert np.allclose(detect._upsample(image, 2)[0, 0], [0.0, 1.0, 2.0, 3.0])


def _scores(path, field, boundary):
    """Write ``field`` and ``boundary`` (uint8, p * 255) as a 10 m UTM GeoTIFF."""
    height, width = field.shape
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=width,
        height=height,
        count=2,
        dtype="uint8",
        crs="EPSG:32615",
        transform=rasterio.transform.from_origin(400_000, 4_650_000, 10, 10),
    ) as dst:
        dst.write(np.stack([field, boundary]))
    return path


def test_polygonize_measures_meters_and_writes_wgs84(tmp_path):
    field = np.zeros((40, 40), dtype=np.uint8)
    field[10:30, 10:30] = 230  # 20 x 20 pixels at 10 m = 40,000 m²
    path = _scores(tmp_path / "scores.tif", field, np.zeros_like(field))

    result = detect.polygonize(
        path, tmp_path / "fields.parquet", simplify=0, min_size=0
    )

    assert result["fields"] == 1
    table = pq.read_table(tmp_path / "fields.parquet")
    assert table.column("metrics:area")[0].as_py() == pytest.approx(40_000)
    geo = json.loads(table.schema.metadata[b"geo"])["columns"]["geometry"]
    assert "crs" not in geo  # GeoParquet's default, OGC:CRS84
    collection = json.loads(table.schema.metadata[b"collection"])
    assert (
        "https://fiboa.org/specification/v0.3.0/schema.yaml"
        in collection["schemas"]["ftw-cli"]
    )
    assert collection["determination:method"] == "auto-imagery"


def test_polygonize_splits_touching_fields_at_the_boundary(tmp_path):
    # Two fields share an edge; a one-pixel predicted boundary between them keeps them apart.
    field = np.zeros((40, 60), dtype=np.uint8)
    field[10:30, 10:50] = 230
    boundary = np.zeros_like(field)
    boundary[10:30, 30] = 230
    field[10:30, 30] = 20
    path = _scores(tmp_path / "scores.tif", field, boundary)

    result = detect.polygonize(
        path, tmp_path / "fields.parquet", simplify=0, min_size=0
    )

    assert result["fields"] == 2


def test_polygonize_rejects_a_single_band_raster(tmp_path):
    path = tmp_path / "classes.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=8,
        height=8,
        count=1,
        dtype="uint8",
        crs="EPSG:32615",
        transform=rasterio.transform.from_origin(0, 0, 10, 10),
    ) as dst:
        dst.write(np.zeros((1, 8, 8), dtype=np.uint8))
    with pytest.raises(ValueError, match="expected field and boundary"):
        detect.polygonize(path, tmp_path / "fields.parquet")


def test_softmax_and_paste_keep_both_score_bands():
    logits = np.zeros((1, 3, 4, 4), dtype=np.float32)
    logits[0, 1] = 10
    probs = detect._softmax(logits)
    assert np.allclose(probs.sum(axis=1), 1) and probs[0, 1].min() > 0.99
    scores = np.zeros((2, 4, 4), dtype=np.uint8)
    detect._paste(
        scores,
        np.full((2, 4, 4), 7, np.uint8),
        detect.Window.from_slices((0, 4), (0, 4)),
        1,
    )
    assert (scores == 7).all()


@pytest.mark.parametrize("suffix", [".gpkg", ".geojson", ".fgb"])
def test_polygonize_writes_gdal_formats_in_wgs84(tmp_path, suffix):
    import duckdb

    field = np.zeros((40, 40), dtype=np.uint8)
    field[10:30, 10:30] = 230
    path = _scores(tmp_path / "scores.tif", field, np.zeros_like(field))
    out = tmp_path / f"fields{suffix}"
    out.write_text("stale")  # an existing file is replaced, not appended to

    assert detect.polygonize(path, out, simplify=0, min_size=0)["fields"] == 1

    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial")
    [(rows, area)] = con.execute(
        'select count(*), any_value("metrics:area") from ST_Read(?)', [str(out)]
    ).fetchall()
    assert (rows, area) == (1, pytest.approx(40_000))
    [(srs,)] = con.execute(
        "select layers[1].geometry_fields[1].crs.auth_code from ST_Read_Meta(?)",
        [str(out)],
    ).fetchall()
    assert srs == "4326"


def test_polygonize_rejects_unknown_formats_before_reading(tmp_path):
    with pytest.raises(ValueError, match="unsupported format '.shp'"):
        detect.polygonize(tmp_path / "missing.tif", tmp_path / "fields.shp")


@pytest.mark.parametrize(("crs", "code"), [("3857", 3857), ("utm", 32615)])
def test_polygonize_writes_the_requested_crs(tmp_path, crs, code):
    import duckdb

    field = np.zeros((40, 40), dtype=np.uint8)
    field[10:30, 10:30] = 230
    path = _scores(tmp_path / "scores.tif", field, np.zeros_like(field))

    parquet = detect.polygonize(path, tmp_path / "f.parquet", 0, 0, crs=crs)
    geo = json.loads(pq.read_schema(tmp_path / "f.parquet").metadata[b"geo"])
    assert parquet["crs"] == f"EPSG:{code}"
    assert geo["columns"]["geometry"]["crs"]["id"]["code"] == code

    detect.polygonize(path, tmp_path / "f.gpkg", 0, 0, crs=crs)
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial")
    [(srs,)] = con.execute(
        "select layers[1].geometry_fields[1].crs.auth_code from ST_Read_Meta(?)",
        [str(tmp_path / "f.gpkg")],
    ).fetchall()
    assert srs == str(code)
