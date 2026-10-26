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
            return tuple(round(a + (b - a) * t) for a, b in zip(c0, c1, strict=True))
    return RAMP[-1][1]


HIGHLIGHT = (255, 127, 65)  # Burning Orange


def render(
    fields: list[dict],
    bbox: BBox,
    width: int = 800,
    fill: tuple[int, int, int] | None = None,
) -> Image.Image:
    """Fields with GeoJSON ``geometry``, drawn over ``bbox``.

    Each field is colored by its ``score``, or with ``fill`` when given.
    """
    lat = math.radians((bbox.ymin + bbox.ymax) / 2)
    aspect = (bbox.ymax - bbox.ymin) / ((bbox.xmax - bbox.xmin) * math.cos(lat))
    w, h = width * SUPERSAMPLE, max(1, round(width * aspect)) * SUPERSAMPLE
    to_px = _projection(bbox, w, h)
    image = Image.new("RGB", (w, h), BACKGROUND)
    draw = ImageDraw.Draw(image)
    for field in fields:
        color = fill or score_color(field.get("score"))
        for exterior, *holes in _polygons(field["geometry"]):
            draw.polygon(
                to_px(exterior), fill=color, outline=OUTLINE, width=SUPERSAMPLE
            )
            for hole in holes:
                draw.polygon(
                    to_px(hole), fill=BACKGROUND, outline=OUTLINE, width=SUPERSAMPLE
                )
    return image.resize((w // SUPERSAMPLE, h // SUPERSAMPLE), Image.Resampling.LANCZOS)


def highlight(image: Image.Image, field: dict, bbox: BBox) -> Image.Image:
    """A copy of ``image`` (from ``render`` over ``bbox``) with ``field`` outlined."""
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    to_px = _projection(bbox, *image.size)
    # Thick enough to survive a terminal's downsampling to character cells.
    width = max(3, image.size[0] // 120)
    for exterior, *_ in _polygons(field["geometry"]):
        draw.polygon(to_px(exterior), fill=(*HIGHLIGHT, 110))
        draw.line([*to_px(exterior), to_px(exterior)[0]], fill=HIGHLIGHT, width=width)
    return Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")


def _projection(bbox: BBox, w: int, h: int):
    def to_px(ring: list[list[float]]) -> list[tuple[float, float]]:
        return [
            (
                (x - bbox.xmin) / (bbox.xmax - bbox.xmin) * w,
                (bbox.ymax - y) / (bbox.ymax - bbox.ymin) * h,
            )
            for x, y, *_ in ring
        ]

    return to_px


def _polygons(geom: dict) -> list:
    return (
        geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    )
