"""Areas: a bbox in degrees, a polygon file, or a place name geocoded with Nominatim."""

import json
import math
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from ftw_cli import USER_AGENT, vectors
from ftw_cli.models import cache_dir

SEARCH_URL = "https://nominatim.openstreetmap.org/search"
# Villages geocode to a few hundred meters, too small to hold fields; widen them to this.
MIN_SIDE_KM = 5.0
# Numbers and commas only: a bbox, even a malformed one, never a place name.
NUMBERS = re.compile(r"^[\d\s,.+-]+$")
VECTOR_SUFFIXES = {".geojson", ".json", ".gpkg", ".parquet", ".fgb", ".shp"}


@dataclass(frozen=True)
class BBox:
    xmin: float
    ymin: float
    xmax: float
    ymax: float
    # The area's polygon (WKT, EPSG:4326) when it is not the whole box, as from a file.
    shape: str | None = field(default=None, repr=False)

    @classmethod
    def parse(cls, text: str) -> "BBox":
        """``xmin,ymin,xmax,ymax`` in degrees."""
        parts = text.split(",")
        if len(parts) != 4:
            raise ValueError(f"bbox needs 4 comma-separated numbers, got {text!r}")
        xmin, ymin, xmax, ymax = (float(p) for p in parts)
        box = cls(xmin, ymin, xmax, ymax)
        if not (
            -180 <= box.xmin < box.xmax <= 180 and -90 <= box.ymin < box.ymax <= 90
        ):
            raise ValueError(
                f"bbox must be xmin<xmax in -180..180, ymin<ymax in -90..90: {text!r}"
            )
        return box

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return self.xmin, self.ymin, self.xmax, self.ymax

    @property
    def center(self) -> tuple[float, float]:
        return (self.xmin + self.xmax) / 2, (self.ymin + self.ymax) / 2

    def __str__(self) -> str:
        return ",".join(f"{v:g}" for v in self.bounds)

    def overlaps(self, prefix: str = "") -> str:
        """SQL: rows whose ``{prefix}xmin..ymax`` bounds intersect this box."""
        p = prefix
        return (
            f"{p}xmax >= {self.xmin} and {p}xmin <= {self.xmax} "
            f"and {p}ymax >= {self.ymin} and {p}ymin <= {self.ymax}"
        )

    def envelope(self) -> str:
        """SQL geometry of the area: its polygon if it has one, else the box."""
        if self.shape:
            return f"ST_GeomFromText('{self.shape}')"
        return f"ST_MakeEnvelope({self.xmin}, {self.ymin}, {self.xmax}, {self.ymax})"

    def area_km2(self) -> float:
        lat = math.radians(self.center[1])
        return (
            (self.xmax - self.xmin)
            * (self.ymax - self.ymin)
            * 111.32**2
            * math.cos(lat)
        )


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
    """The union of a vector file's polygons, in EPSG:4326; no CRS means EPSG:4326."""
    if not path.is_file():
        raise FileNotFoundError(f"no such file: {path}")
    rows = (
        vectors.connect()
        .execute(
            f"""
        with area as (
            select ST_Union_Agg(ST_Transform(
                geometry, coalesce(ST_CRS(geometry), 'EPSG:4326'), 'EPSG:4326'
            )) as shape
            from {vectors.source(path)}
        )
        select ST_AsText(shape), ST_XMin(shape), ST_YMin(shape), ST_XMax(shape), ST_YMax(shape)
        from area where ST_Area(shape) > 0
        """
        )
        .fetchall()
    )
    if not rows:
        raise ValueError(f"{path} has no polygons")
    [(wkt, *bounds)] = rows
    return Place(path.name, BBox(*bounds, shape=wkt))


def geocode(query: str) -> Place:
    """The best Nominatim match for ``query``, cached as its usage policy asks."""
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
    cx, cy = bbox.center
    half_y = MIN_SIDE_KM / 2 / 111.32
    half_x = half_y / max(math.cos(math.radians(cy)), 0.01)
    return BBox(
        max(-180.0, min(bbox.xmin, cx - half_x)),
        max(-90.0, min(bbox.ymin, cy - half_y)),
        min(180.0, max(bbox.xmax, cx + half_x)),
        min(90.0, max(bbox.ymax, cy + half_y)),
    )
