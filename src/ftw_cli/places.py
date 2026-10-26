"""Place names to bounding boxes, through OpenStreetMap's Nominatim geocoder."""

import json
import math
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass

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


@dataclass(frozen=True)
class Place:
    name: str
    bbox: BBox


def is_bbox(text: str) -> bool:
    return bool(NUMBERS.match(text))


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
