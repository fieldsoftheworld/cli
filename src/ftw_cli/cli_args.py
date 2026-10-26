"""Arguments shared by the ``ftw`` commands."""

import importlib.util
from pathlib import Path
from typing import Annotated

from cyclopts import Parameter, validators

from ftw_cli import areas, output
from ftw_cli.areas import BBox

AreaArg = Annotated[
    str,
    Parameter(
        allow_leading_hyphen=True,
        help='Place name ("Story County, Iowa"), xmin,ymin,xmax,ymax in degrees, '
        "or a polygon file.",
    ),
]
YearArg = Annotated[int | None, Parameter(help="Year (2017-2025). Default: latest.")]
YearsArg = Annotated[
    str | None,
    Parameter(
        help="Year, range (2019..2024), list (2018,2021), or all. Default: latest."
    ),
]
CrsArg = Annotated[str, Parameter(help="Output CRS: an EPSG code, or utm.")]
FieldsOut = Annotated[
    Path, Parameter(help="Output fields: .parquet (fiboa), .gpkg, .geojson, or .fgb.")
]
FieldsFile = Annotated[
    Path,
    Parameter(
        validator=validators.Path(exists=True, dir_okay=False),
        help="Fields file: .parquet, .gpkg, .geojson, or .fgb.",
    ),
]
MinArea = Annotated[float | None, Parameter(help="Minimum field area, m².")]
MaxArea = Annotated[float | None, Parameter(help="Maximum field area, m².")]
MinScore = Annotated[float | None, Parameter(help="Minimum field score, 0-100.")]

EXTRAS = {
    "imagery": ("rasterio", "pystac_client", "planetary_computer"),
    "inference": (
        "rasterio",
        "pystac_client",
        "planetary_computer",
        "onnxruntime",
        "fbp",
    ),
    "stats": ("rasterio", "exactextract"),
}
LARGE_AREA_KM2 = 10_000


def parse_area(text: str) -> BBox:
    """AREA as a bbox; a file or place name is echoed to stderr with its bbox."""
    if areas.is_bbox(text):
        return BBox.parse(text)
    path = Path(text)
    place = areas.from_file(path) if areas.is_file(text) else areas.geocode(text)
    output.stderr.print(f"[dim]{place.name}  →  {place.bbox}[/dim]")
    if (km2 := place.bbox.area_km2()) > LARGE_AREA_KM2:
        output.stderr.print(f"[yellow]{km2:,.0f} km² may read several GB.[/yellow]")
    return place.bbox


def missing(extra: str) -> list[str]:
    return [m for m in EXTRAS[extra] if importlib.util.find_spec(m) is None]


def require(extra: str) -> None:
    if gone := missing(extra):
        raise SystemExit(
            f"error: this command needs the {extra} extra ({', '.join(gone)} "
            f"missing): pip install 'ftw-cli[{extra}]'"
        )
