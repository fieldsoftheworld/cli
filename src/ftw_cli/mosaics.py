"""Detect fields tile by tile on quarterly mosaics, the way the FTW 2nd Edition was made.

Imagery comes from the anonymous Source Cooperative mirror of the Copernicus Sentinel-2
Global Mosaics, found through the mirror's per-quarter STAC GeoParquet. Each tile reads only
its part of the area plus a halo, and keeps the fields whose centroid lies in its own 100 km
MGRS square and UTM zone, so a field on a seam is kept once.
"""

import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np
import rasterio
from rasterio.transform import Affine, from_origin
from rasterio.warp import transform_bounds
from rasterio.windows import Window, from_bounds

from ftw_cli import USER_AGENT, cog
from ftw_cli.areas import BBox
from ftw_cli.progress import Progress

MIRROR = "https://data.source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics"
QUARTERS = ("Q1", "Q2", "Q3", "Q4")
BANDS = ("B04", "B03", "B02", "B08")
# Context read around the area on each side, in 10 m pixels: one model patch, 1.28 km, as
# the 2nd Edition's 512 px halo at 2.5 m.
HALO = 128
SQUARE_M = 100_000.0


@dataclass(frozen=True)
class Tile:
    """An MGRS sub-tile of the mosaics, such as ``15TVG_0_0``, and its 10 m grid."""

    key: str
    crs: str
    transform: Affine
    height: int
    width: int

    def href(self, year: int, quarter: str, band: str) -> str:
        return f"{MIRROR}/{year}/{quarter}/{self.key}/{band}.tif"


@dataclass(frozen=True)
class Part:
    """The window of one tile that covers an area, and where that window sits."""

    tile: Tile
    year: int
    window: Window
    transform: Affine  # of the window, at 10 m
    crs: str
    square: tuple[float, float, float, float]  # the tile's own 100 km MGRS cell
    lon_range: tuple[float, float]  # the tile's UTM zone

    @property
    def scores_transform(self) -> Affine:
        """The window's transform at the model's 2.5 m output."""
        return self.transform @ Affine.scale(0.25)

    @property
    def stamp(self) -> str:
        """Identifies the window, so kept scores are reused only for the same one."""
        w = self.window
        return (
            f"{self.tile.key} {self.year} {w.col_off},{w.row_off},{w.width},{w.height}"
        )


def tiles(con: duckdb.DuckDBPyConnection, bbox: BBox, year: int) -> list[Tile]:
    """The tiles over ``bbox`` with all four quarters, from the mirror's STAC GeoParquet.

    The tile grid doesn't change by year, so a year whose index isn't published yet uses
    the footprints of the latest year that is; its imagery is still that year's.
    """
    published = _published_years()
    if not published:
        raise LookupError("the mosaic mirror lists no complete year of STAC items")
    source = year if year in published else max(published)
    urls = [f"{MIRROR}/mosaics/quarter={source}.{q}/items.parquet" for q in QUARTERS]
    rows = con.execute(
        """
        select _subtile, any_value("proj:code"), any_value("proj:bbox"), any_value("proj:shape")
        from read_parquet(?)
        where bbox[3] >= ? and bbox[1] <= ? and bbox[4] >= ? and bbox[2] <= ?
        group by 1 having count(distinct quarter) = 4 order by 1
        """,
        [urls, bbox.xmin, bbox.xmax, bbox.ymin, bbox.ymax],
    ).fetchall()
    if not rows:
        raise LookupError(
            f"no quarterly mosaic tile with all four quarters covers {bbox}"
        )
    return [
        Tile(key, crs, from_origin(x0, y1, (x1 - x0) / w, (y1 - y0) / h), h, w)
        for key, crs, (x0, y0, x1, y1), (h, w) in rows
    ]


def locate(tile: Tile, year: int, bbox: BBox) -> Part | None:
    """The part of ``tile`` over ``bbox`` plus the halo, or None if they don't overlap."""
    full, height, width, crs = tile.transform, tile.height, tile.width, tile.crs
    box = from_bounds(*transform_bounds("EPSG:4326", crs, *bbox.bounds), full)
    r0, c0 = max(int(box.row_off) - HALO, 0), max(int(box.col_off) - HALO, 0)
    r1 = min(int(box.row_off + box.height) + 1 + HALO, height)
    c1 = min(int(box.col_off + box.width) + 1 + HALO, width)
    if r1 <= r0 or c1 <= c0:
        return None
    window = Window(c0, r0, c1 - c0, r1 - r0)  # ty: ignore[too-many-positional-arguments]
    return Part(
        tile,
        year,
        window,
        full @ Affine.translation(c0, r0),
        crs,
        _square(full),
        _lon_range(tile),
    )


def read(part: Part, progress: Progress | None = None) -> np.ndarray:
    """The part's 16 bands, Q1-Q4 x B04/B03/B02/B08, read at once on threads."""

    def band(href: str) -> np.ndarray:
        with rasterio.open(href) as ds:
            return ds.read(1, window=part.window)

    hrefs = [part.tile.href(part.year, q, b) for q in QUARTERS for b in BANDS]
    with ThreadPoolExecutor(len(hrefs)) as pool:
        futures = [pool.submit(band, h) for h in hrefs]
        for done, _ in enumerate(as_completed(futures), 1):
            if progress:
                progress(done, len(hrefs))
    return np.stack([f.result() for f in futures])


def kept(path: Path, part: Part, model: str) -> np.ndarray | None:
    """Scores kept by an earlier run for the same window and model, if there are any."""
    if not path.exists():
        return None
    with rasterio.open(path) as ds:
        tags = ds.tags()
        if tags.get("FTW_WINDOW") != part.stamp or tags.get("FTW_MODEL") != model:
            return None
        return ds.read()


def owned(part: Part) -> str:
    """SQL predicate over ``geom`` (in the part's CRS): the fields this tile keeps."""
    x0, y0, x1, y1 = part.square
    w, e = part.lon_range
    lon = f"ST_X(ST_Transform(ST_Centroid(geom), '{part.crs}', 'EPSG:4326'))"
    return (
        f"ST_X(ST_Centroid(geom)) >= {x0} and ST_X(ST_Centroid(geom)) < {x1} "
        f"and ST_Y(ST_Centroid(geom)) > {y0} and ST_Y(ST_Centroid(geom)) <= {y1} "
        f"and {lon} >= {w} and {lon} < {e}"
    )


def save(path: Path, scores: np.ndarray, part: Part, model: str) -> None:
    """Keep a part's scores as a 2.5 m COG that a later run can reuse."""
    cog.write(
        path,
        scores,
        {
            "crs": part.crs,
            "transform": part.scores_transform,
            "width": scores.shape[2],
            "height": scores.shape[1],
        },
        tags={"FTW_MODEL": model, "FTW_WINDOW": part.stamp},
        descriptions=("field", "boundary"),
    )


def _published_years() -> set[int]:
    """Years whose four quarter partitions the mosaic collection links to."""
    request = urllib.request.Request(
        f"{MIRROR}/mosaics/collection.json", headers={"User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        links = json.load(response)["links"]
    quarters = {
        link["href"].split("=")[1].split("/")[0]
        for link in links
        if link["rel"] == "item"
    }
    years = {int(q.split(".")[0]) for q in quarters}
    return {y for y in years if all(f"{y}.{q}" in quarters for q in QUARTERS)}


def _square(full: Affine) -> tuple[float, float, float, float]:
    """The 100 km cell the raster's north-west corner snaps to, as the 2nd Edition owns it."""
    x0 = round(full.c / SQUARE_M) * SQUARE_M
    y1 = round(full.f / SQUARE_M) * SQUARE_M
    return x0, y1 - SQUARE_M, x0 + SQUARE_M, y1


def _lon_range(tile: Tile) -> tuple[float, float]:
    """The tile's UTM zone in longitude, with the 31V/32V exception the MGRS grid makes."""
    zone, band = int(tile.key[:2]), tile.key[2]
    if band == "V" and zone in (31, 32):
        return (0.0, 3.0) if zone == 31 else (3.0, 12.0)
    return -180.0 + 6 * (zone - 1), -180.0 + 6 * zone
