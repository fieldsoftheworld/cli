"""Arguments shared by the ``ftw`` commands."""

import importlib.util
from typing import Annotated

from cyclopts import Parameter

from ftw_cli import output, places
from ftw_cli.ftw2e import BBox

AreaArg = Annotated[
    str,
    Parameter(
        name=["area", "--bbox", "--place"],
        allow_leading_hyphen=True,
        help='A place name ("Story County, Iowa") or xmin,ymin,xmax,ymax in degrees.',
    ),
]
YearArg = Annotated[
    int | None, Parameter(help="Dataset year (2017-2025). Defaults to the latest.")
]
EXTRAS = {
    "imagery": ("rasterio", "pystac_client", "planetary_computer"),
    "inference": (
        "rasterio",
        "pystac_client",
        "planetary_computer",
        "onnxruntime",
        "fbp",
    ),
}
CrsArg = Annotated[
    str,
    Parameter(
        help='CRS to write: an EPSG code such as 3857, or "utm" to keep UTM meters.'
    ),
]
LARGE_AREA_KM2 = 10_000


def parse_area(text: str) -> BBox:
    """A bbox from AREA, geocoding it when it is not four numbers."""
    if places.is_bbox(text):
        return BBox.parse(text)
    place = places.geocode(text)
    output.stderr.print(f"[dim]{place.name}  →  {place.bbox}[/dim]")
    if (km2 := place.bbox.area_km2()) > LARGE_AREA_KM2:
        output.stderr.print(f"[yellow]{km2:,.0f} km² may read several GB.[/yellow]")
    return place.bbox


def require(extra: str) -> None:
    if missing := [m for m in EXTRAS[extra] if importlib.util.find_spec(m) is None]:
        raise SystemExit(
            f"error: this command needs the {extra} extra ({', '.join(missing)} "
            f"missing): pip install 'ftw-cli[{extra}]'"
        )
