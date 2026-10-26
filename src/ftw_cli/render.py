"""Draw field boundaries to an image, colored by score in the FTW palette."""

import math
from itertools import pairwise

from PIL import Image, ImageDraw

from ftw_cli.ftw2e import BBox

BACKGROUND = (0, 56, 56)  # a shade darker than FTW Cyprus
OUTLINE = (0, 71, 71)  # Cyprus
# Score ramp: dim meadow, Mountain Meadow, Yellow Green.
RAMP = [(0, (24, 104, 84)), (50, (34, 160, 112)), (100, (192, 216, 91))]
SUPERSAMPLE = 2


def score_color(score: float | None) -> tuple[int, int, int]:
    s = 0.0 if score is None else max(0.0, min(100.0, float(score)))
    for (s0, c0), (s1, c1) in pairwise(RAMP):
        if s <= s1:
            t = (s - s0) / (s1 - s0)
            r, g, b = (round(a + (b - a) * t) for a, b in zip(c0, c1, strict=True))
            return r, g, b
    return RAMP[-1][1]


def render(fields: list[dict], bbox: BBox, width: int = 800) -> Image.Image:
    """Fields with GeoJSON ``geometry`` and ``score``, drawn over ``bbox``."""
    lat = math.radians((bbox.ymin + bbox.ymax) / 2)
    aspect = (bbox.ymax - bbox.ymin) / ((bbox.xmax - bbox.xmin) * math.cos(lat))
    w, h = width * SUPERSAMPLE, max(1, round(width * aspect)) * SUPERSAMPLE

    def to_px(ring: list[list[float]]) -> list[tuple[float, float]]:
        return [
            (
                (x - bbox.xmin) / (bbox.xmax - bbox.xmin) * w,
                (bbox.ymax - y) / (bbox.ymax - bbox.ymin) * h,
            )
            for x, y, *_ in ring
        ]

    image = Image.new("RGB", (w, h), BACKGROUND)
    draw = ImageDraw.Draw(image)
    for field in fields:
        geom = field["geometry"]
        polygons = (
            geom["coordinates"]
            if geom["type"] == "MultiPolygon"
            else [geom["coordinates"]]
        )
        fill = score_color(field.get("score"))
        for exterior, *holes in polygons:
            draw.polygon(to_px(exterior), fill=fill, outline=OUTLINE, width=SUPERSAMPLE)
            for hole in holes:
                draw.polygon(
                    to_px(hole), fill=BACKGROUND, outline=OUTLINE, width=SUPERSAMPLE
                )
    return image.resize((w // SUPERSAMPLE, h // SUPERSAMPLE), Image.Resampling.LANCZOS)
