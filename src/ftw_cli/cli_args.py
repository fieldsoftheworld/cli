"""Argument types and error handling shared by the ``ftw`` commands."""

import sys
from typing import Annotated, NoReturn

from cyclopts import Parameter

from ftw_cli import ftw2e, output

AreaArg = Annotated[
    str,
    Parameter(
        name=["area", "--bbox", "--place"],
        allow_leading_hyphen=True,
        help='A place name ("Story County, Iowa") or xmin,ymin,xmax,ymax in EPSG:4326 degrees.',
    ),
]
# Above this, a query reads many partition files; say so before the wait starts.
LARGE_AREA_KM2 = 10_000
YearArg = Annotated[
    int | None, Parameter(help="Dataset year (2017-2025). Defaults to the latest.")
]


def parse_area(text: str) -> ftw2e.BBox:
    """A bbox from AREA, geocoding it when it is not four numbers."""
    from ftw_cli import places

    try:
        if places.is_bbox(text):
            return ftw2e.BBox.parse(text)
        place = places.geocode(text)
    except ValueError as e:
        fail(str(e))
    except (LookupError, OSError) as e:
        fail(f"could not find {text!r}: {e}")
    box = place.bbox
    corners = ",".join(f"{v:.4f}" for v in vars(box).values())
    output.stderr.print(f"[dim]{place.name}  →  {corners}[/dim]")
    if (km2 := box.area_km2()) > LARGE_AREA_KM2:
        output.stderr.print(
            f"[yellow]{km2:,.0f} km² is a large area; this may read several GB.[/yellow]"
        )
    return box


def fail(message: str, code: int = 2) -> NoReturn:
    output.stderr.print(f"[red]error:[/red] {message}")
    sys.exit(code)


def need(extra: str, error: ImportError) -> NoReturn:
    fail(
        f"this command needs the {extra} extra ({error.name} is missing): pip install 'ftw-cli[{extra}]'"
    )
