"""Areas from place names, through OpenStreetMap's Nominatim geocoder, or from polygon files."""

import json
import math
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import duckdb

from ftw_cli import __version__
from ftw_cli.ftw2e import BBox
from ftw_cli.models import cache_dir

SEARCH_URL = "https://nominatim.openstreetmap.org/search"
# Nominatim's usage policy asks for an identifying User-Agent and cached results.
USER_AGENT = f"ftw-cli/{__version__} (https://github.com/fieldsoftheworld/cli)"
# Villages geocode to a few hundred meters, too small to hold fields; widen them to this.
MIN_SIDE_KM = 5.0
# Numbers and commas only: a bbox, even a malformed one, never a place name.
NUMBERS = re.compile(r"^[\d\s,.+-]+$")
VECTOR_SUFFIXES = {".geojson", ".json", ".gpkg", ".parquet", ".fgb", ".shp"}


@dataclass(frozen=True)
class Place:
    name: str
    bbox: BBox


def is_bbox(text: str) -> bool:
    return bool(NUMBERS.match(text))


def is_file(text: str) -> bool:
    path = Path(text)
    return path.suffix.lower() in VECTOR_SUFFIXES or path.is_file()


def from_file(path: Path) -> Place:
    """The union of the polygons in a vector file, in EPSG:4326."""
    if not path.is_file():
        raise FileNotFoundError(f"no such file: {path}")
    parquet = path.suffix.lower() == ".parquet"
    source = f"read_parquet('{_sql(path)}')" if parquet else f"ST_Read('{_sql(path)}')"
    column = "geometry" if parquet else "geom"
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial; SET geometry_always_xy = true")
    # Files without a CRS (a shapefile with no .prj) are taken to be EPSG:4326.
    rows = con.execute(
        f"""
        with area as (
            select ST_Union_Agg(ST_Transform(
                {column}, coalesce(ST_CRS({column}), 'EPSG:4326'), 'EPSG:4326'
            )) as shape
            from {source}
        )
        select ST_AsText(shape), ST_XMin(shape), ST_YMin(shape), ST_XMax(shape), ST_YMax(shape)
        from area where ST_Area(shape) > 0
        """
    ).fetchall()
    if not rows:
        raise ValueError(f"{path} has no polygons")
    [(wkt, *bounds)] = rows
    return Place(path.name, BBox(*bounds, shape=wkt))


def geocode(query: str) -> Place:
    """The best match for ``query``, from the cache or from Nominatim."""
    key = " ".join(query.lower().split())
    path = cache_dir() / "places.json"
    cache = json.loads(path.read_text()) if path.exists() else {}
    if key not in cache:
        url = f"{SEARCH_URL}?" + urllib.parse.urlencode(
            {"q": query, "format": "jsonv2", "limit": 1}
        )
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=20) as response:
            hits = json.load(response)
        if not hits:
            raise LookupError(f"no place matches {query!r}")
        south, north, west, east = (float(v) for v in hits[0]["boundingbox"])
        cache[key] = {
            "name": hits[0]["display_name"],
            "bbox": [west, south, east, north],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, indent=1))
    hit = cache[key]
    return Place(hit["name"], _widen(BBox(*hit["bbox"])))


def _widen(bbox: BBox) -> BBox:
    lat = (bbox.ymin + bbox.ymax) / 2
    half_y = MIN_SIDE_KM / 2 / 111.32
    half_x = half_y / max(math.cos(math.radians(lat)), 0.01)
    cx, cy = (bbox.xmin + bbox.xmax) / 2, (bbox.ymin + bbox.ymax) / 2
    return BBox(
        max(-180.0, min(bbox.xmin, cx - half_x)),
        max(-90.0, min(bbox.ymin, cy - half_y)),
        min(180.0, max(bbox.xmax, cx + half_x)),
        min(90.0, max(bbox.ymax, cy + half_y)),
    )


def _sql(path: Path) -> str:
    return str(path).replace("'", "''")
