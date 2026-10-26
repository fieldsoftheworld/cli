import json

import duckdb
import pytest

from ftw_cli import ftw2e, places
from ftw_cli.cli_args import parse_area

# A triangle over part of Ames, Iowa: the lower-left half of its bbox.
TRIANGLE = [[-93.70, 41.95], [-93.60, 41.95], [-93.70, 42.05], [-93.70, 41.95]]


@pytest.fixture
def con():
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial; SET geometry_always_xy = true")
    return con


@pytest.fixture
def geojson(tmp_path):
    path = tmp_path / "area.geojson"
    feature = {"type": "Polygon", "coordinates": [TRIANGLE]}
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {"type": "Feature", "properties": {}, "geometry": feature}
                ],
            }
        )
    )
    return path


def test_polygon_files_read_to_the_same_wgs84_area(con, geojson, tmp_path):
    for suffix, options in [
        (".gpkg", "format gdal, driver 'GPKG', srs 'EPSG:3857'"),
        (".parquet", "format parquet"),
    ]:
        column = "geom" if suffix == ".gpkg" else "geometry"
        con.execute(
            f"""copy (select ST_SetCRS(ST_Transform(geom, 'EPSG:4326', 'EPSG:3857'), 'EPSG:3857')
                      as {column} from ST_Read('{geojson}'))
                to '{tmp_path / f"area{suffix}"}' ({options})"""
        )
    boxes = [
        places.from_file(p).bbox
        for p in (geojson, tmp_path / "area.gpkg", tmp_path / "area.parquet")
    ]
    for box in boxes:
        assert box.bounds == pytest.approx((-93.70, 41.95, -93.60, 42.05), abs=1e-6)
        assert box.shape and box.shape.startswith("POLYGON")
    assert "ST_GeomFromText" in boxes[0].envelope()


def test_area_arguments_pick_bbox_file_or_place(geojson, tmp_path, monkeypatch):
    monkeypatch.setattr(places, "geocode", lambda q: pytest.fail("geocoded a file"))
    assert parse_area(str(geojson)).shape
    assert parse_area("-93.7,41.95,-93.6,42.05").shape is None
    with pytest.raises(FileNotFoundError, match="no such file"):
        parse_area(str(tmp_path / "missing.geojson"))


def test_queries_keep_only_fields_in_the_polygon(con, geojson):
    # One field inside the triangle, one inside its bbox but outside the triangle.
    con.execute(
        """create table fields as
           select id, geometry, struct_pack(xmin := ST_XMin(geometry), ymin := ST_YMin(geometry),
                                            xmax := ST_XMax(geometry), ymax := ST_YMax(geometry)) as bbox
           from (values ('inside', ST_MakeEnvelope(-93.69, 41.96, -93.68, 41.97)),
                        ('corner', ST_MakeEnvelope(-93.62, 42.03, -93.61, 42.04))) t(id, geometry)"""
    )
    area = places.from_file(geojson).bbox
    box = ftw2e.BBox(*area.bounds)
    pick = "select id from fields where {} order by id"
    assert con.execute(pick.format(ftw2e._in_bbox(box))).fetchall() == [
        ("corner",),
        ("inside",),
    ]
    assert con.execute(pick.format(ftw2e._in_bbox(area))).fetchall() == [("inside",)]
