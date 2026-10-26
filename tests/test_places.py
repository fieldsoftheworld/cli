import io
import json

import pytest

from ftw_cli import places
from ftw_cli.cli_args import parse_area
from ftw_cli.ftw2e import BBox


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
        ("1,2,3", False),
    ],
)
def test_is_bbox(text, expected):
    assert places.is_bbox(text) is expected


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

    monkeypatch.setattr(places.urllib.request, "urlopen", urlopen)
    first = places.geocode("Story County,  Iowa")
    second = places.geocode("story county, iowa")

    assert (
        first
        == second
        == places.Place("Story County", BBox(-93.70, 41.86, -93.23, 42.21))
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
        places.urllib.request,
        "urlopen",
        lambda request, timeout: io.BytesIO(json.dumps(hit).encode()),
    )
    box = places.geocode("a village").bbox
    assert box.ymax - box.ymin == pytest.approx(places.MIN_SIDE_KM / 111.32)
    assert box.xmin < 1.6 and box.xmax > 1.601


def test_unknown_place_fails_cleanly(monkeypatch, capsys):
    monkeypatch.setattr(
        places.urllib.request, "urlopen", lambda request, timeout: io.BytesIO(b"[]")
    )
    with pytest.raises(SystemExit):
        parse_area("Nowhere At All")
    assert "could not find 'Nowhere At All'" in capsys.readouterr().err


def test_parse_area_takes_numbers_without_geocoding(monkeypatch):
    monkeypatch.setattr(places, "geocode", lambda q: pytest.fail("geocoded a bbox"))
    assert parse_area("-93.7,41.95,-93.6,42.05") == BBox(-93.7, 41.95, -93.6, 42.05)
