import io
import json

import pytest

from ftw_cli import areas, ftw2e, vectors
from ftw_cli.areas import BBox
from ftw_cli.cli_args import parse_area

# A triangle over part of Ames, Iowa: the lower-left half of its bbox.
TRIANGLE = [[-93.70, 41.95], [-93.60, 41.95], [-93.70, 42.05], [-93.70, 41.95]]


@pytest.fixture
def con():
    return vectors.connect()


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
        areas.from_file(p).bbox
        for p in (geojson, tmp_path / "area.gpkg", tmp_path / "area.parquet")
    ]
    for box in boxes:
        assert box.bounds == pytest.approx((-93.70, 41.95, -93.60, 42.05), abs=1e-6)
        assert box.shape and box.shape.startswith("POLYGON")
    assert "ST_GeomFromText" in boxes[0].envelope()


def test_area_arguments_pick_bbox_file_or_place(geojson, tmp_path, monkeypatch):
    monkeypatch.setattr(areas, "geocode", lambda q: pytest.fail("geocoded a file"))
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
    area = areas.from_file(geojson).bbox
    box = BBox(*area.bounds)
    pick = "select id from fields where {} order by id"
    assert con.execute(pick.format(ftw2e._in_bbox(box))).fetchall() == [
        ("corner",),
        ("inside",),
    ]
    assert con.execute(pick.format(ftw2e._in_bbox(area))).fetchall() == [("inside",)]


@pytest.mark.parametrize(
    "text",
    [
        "-93.6,41.95,-93.7,42.05",  # xmin > xmax
        "41.95,-93.7,42.05,-93.6",  # lat/lon swapped
        "-93.7,41.95,-93.6",
    ],
)
def test_bbox_rejects_malformed(text: str):
    with pytest.raises(ValueError):
        BBox.parse(text)


@pytest.fixture(autouse=True)
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("FTW_CACHE_DIR", str(tmp_path))
    return tmp_path


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("-93.7,41.95,-93.6,42.05", True),
        (" 1.55, 48.2 ,1.7,48.3 ", True),
        ("Ames, Iowa", False),
        ("1,2,3", True),  # malformed, but still a bbox, not a place
    ],
)
def test_is_bbox(text, expected):
    assert areas.is_bbox(text) is expected


def test_geocode_reads_nominatim_axis_order_and_caches(cache, monkeypatch):
    # Nominatim's boundingbox is [south, north, west, east].
    hit = [
        {
            "display_name": "Story County",
            "boundingbox": ["41.86", "42.21", "-93.70", "-93.23"],
        }
    ]
    calls = []

    def urlopen(request, timeout):
        calls.append(request.full_url)
        return io.BytesIO(json.dumps(hit).encode())

    monkeypatch.setattr(areas.urllib.request, "urlopen", urlopen)
    first = areas.geocode("Story County,  Iowa")
    second = areas.geocode("story county, iowa")

    assert (
        first
        == second
        == areas.Place("Story County", BBox(-93.70, 41.86, -93.23, 42.21))
    )
    assert len(calls) == 1  # the second lookup is served from the cache
    assert (cache / "places.json").exists()


def test_geocode_widens_a_village_to_a_useful_box(monkeypatch):
    hit = [
        {
            "display_name": "A village",
            "boundingbox": ["48.250", "48.251", "1.600", "1.601"],
        }
    ]
    monkeypatch.setattr(
        areas.urllib.request,
        "urlopen",
        lambda request, timeout: io.BytesIO(json.dumps(hit).encode()),
    )
    box = areas.geocode("a village").bbox
    assert box.ymax - box.ymin == pytest.approx(areas.MIN_SIDE_KM / 111.32)
    assert box.xmin < 1.6 and box.xmax > 1.601


def test_unknown_place_raises(monkeypatch):
    monkeypatch.setattr(
        areas.urllib.request, "urlopen", lambda request, timeout: io.BytesIO(b"[]")
    )
    with pytest.raises(LookupError, match="no place matches"):
        parse_area("Nowhere At All")


def test_parse_area_takes_numbers_without_geocoding(monkeypatch):
    monkeypatch.setattr(areas, "geocode", lambda q: pytest.fail("geocoded a bbox"))
    assert parse_area("-93.7,41.95,-93.6,42.05") == BBox(-93.7, 41.95, -93.6, 42.05)
