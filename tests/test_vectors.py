import duckdb
import pytest

from ftw_cli import vectors


def test_utm_zone_in_both_hemispheres():
    assert vectors.utm_zone(-93.6, 42.0) == "EPSG:32615"  # Iowa
    assert vectors.utm_zone(30.3, -2.0) == "EPSG:32736"  # Rwanda


def test_crs_normalizes_codes_and_rejects_unknown_ones():
    assert vectors.crs("3857", utm="EPSG:32615") == "EPSG:3857"
    assert vectors.crs("UTM", utm="EPSG:32615") == "EPSG:32615"
    with pytest.raises(duckdb.Error):
        vectors.crs("999999", utm="EPSG:32615")


def test_write_keeps_the_largest_polygon_of_a_multipart_field(tmp_path):
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial; SET geometry_always_xy = true")
    query = """
        select * from (values
            ('a', ST_GeomFromText('MULTIPOLYGON(((0 0,1 0,1 1,0 1,0 0)),((5 5,8 5,8 8,5 8,5 5)))')),
            ('b', ST_GeomFromText('LINESTRING(0 0,1 1)'))
        ) t(id, geometry)
    """
    out = tmp_path / "fields.geojson"
    assert vectors.write(con, query, out, {}) == 1  # a line is not a field
    [(kind, area)] = con.execute(
        "select ST_GeometryType(geom), ST_Area(geom) from ST_Read(?)", [str(out)]
    ).fetchall()
    assert (str(kind), area) == ("POLYGON", 9)
