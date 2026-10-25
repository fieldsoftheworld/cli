"""Argument types and error handling shared by the ``ftw`` commands."""

import sys
from typing import Annotated, NoReturn

from cyclopts import Parameter

from ftw_cli import ftw2e, output

BBoxArg = Annotated[
    str,
    Parameter(
        allow_leading_hyphen=True,
        help="xmin,ymin,xmax,ymax in EPSG:4326 degrees, e.g. -93.7,41.95,-93.6,42.05.",
    ),
]
YearArg = Annotated[
    int | None, Parameter(help="Dataset year (2017-2025). Defaults to the latest.")
]


def parse_bbox(text: str) -> ftw2e.BBox:
    try:
        return ftw2e.BBox.parse(text)
    except ValueError as e:
        fail(str(e))


def fail(message: str, code: int = 2) -> NoReturn:
    output.stderr.print(f"[red]error:[/red] {message}")
    sys.exit(code)


def need(extra: str, error: ImportError) -> NoReturn:
    fail(
        f"this command needs the {extra} extra ({error.name} is missing): pip install 'ftw-cli[{extra}]'"
    )
