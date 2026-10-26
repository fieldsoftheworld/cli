"""Pick and download the two Sentinel-2 scenes FTW models take, using the crop calendar.

Ported from ftw-baselines' ``download_img.py`` with the same scene selection and output
layout, using rasterio warped reads in place of odc-stac, dask, and xarray.
"""

import math
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import duckdb
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds

from ftw_cli.ftw2e import BBox

CROP_CALENDAR = "https://data.source.coop/ftw/ftw-inference-input/global-crop-calendar/"
SEASON_START = CROP_CALENDAR + "sc-sos-3x3-v2-cog.tiff"
SEASON_END = CROP_CALENDAR + "sc-eos-3x3-v2-cog.tiff"

HOSTS = {
    "mspc": ("https://planetarycomputer.microsoft.com/api/stac/v1", "sentinel-2-l2a"),
    "earthsearch": ("https://earth-search.aws.element84.com/v1", "sentinel-2-c1-l2a"),
}
# Red, green, blue, NIR, in the order the models were trained on.
BANDS = {
    "mspc": ["B04", "B03", "B02", "B08"],
    "earthsearch": ["red", "green", "blue", "nir"],
}
RESOLUTION = 10.0
# Scenes with more of the AOI than this outside their footprint are skipped.
MAX_AOI_NODATA = 50.0


@dataclass(frozen=True)
class Scene:
    id: str
    date: str
    cloud_cover: float
    aoi_nodata: float
    href: str


def season(bbox: BBox, year: int) -> tuple[date, date]:
    """Start and end of the summer growing season over ``bbox`` from the crop calendar."""
    start, end = _day_of_year(SEASON_START, bbox), _day_of_year(SEASON_END, bbox)
    # A season that ends before it starts runs into the next year (southern hemisphere).
    end_year = year + 1 if end < start else year
    return _from_day(year, start), _from_day(end_year, end)


def search(
    bbox: BBox,
    when: date,
    host: str = "mspc",
    buffer_days: int = 14,
    cloud_cover_max: float = 20,
) -> Scene:
    """The least cloudy scene within ``buffer_days`` of ``when`` that covers most of ``bbox``."""
    import pystac_client

    start, end = when - timedelta(days=buffer_days), when + timedelta(days=buffer_days)
    if end > date.today():
        raise ValueError(f"{start}..{end} reaches into the future; try an earlier year")
    url, collection = HOSTS[host]
    items = (
        pystac_client.Client.open(url)
        .search(
            collections=[collection],
            bbox=list(vars(bbox).values()),
            datetime=f"{start}T00:00:00Z/{end}T23:59:59Z",
            query={"eo:cloud_cover": {"lt": cloud_cover_max}},
        )
        .item_collection()
    )
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    scenes = [
        Scene(
            id=item.id,
            date=item.datetime.date().isoformat(),
            cloud_cover=item.properties["eo:cloud_cover"],
            aoi_nodata=_aoi_nodata(con, item.geometry, bbox),
            href=item.get_self_href(),
        )
        for item in items
    ]
    usable = [s for s in scenes if s.aoi_nodata <= MAX_AOI_NODATA]
    if not usable:
        raise LookupError(
            f"no {host} scene within {buffer_days} days of {when} has under "
            f"{cloud_cover_max}% cloud and covers the area ({len(scenes)} found)"
        )
    return min(usable, key=lambda s: s.cloud_cover)


def download(
    scenes: list[Scene],
    bbox: BBox,
    out: Path,
    host: str = "mspc",
    progress: Callable[[int, int | None], None] | None = None,
) -> dict:
    """Stack the RGBN bands of ``scenes`` over ``bbox`` into one uint16 GeoTIFF at 10 m.

    The bands are read at once on threads, since each is a separate remote COG.
    """
    import pystac

    items = [pystac.Item.from_file(s.href) for s in scenes]
    if host == "mspc":
        import planetary_computer

        items = [planetary_computer.sign(i) for i in items]
    crs = (
        f"EPSG:{items[0].properties['proj:epsg']}"
        if "proj:epsg" in items[0].properties
        else items[0].properties["proj:code"]
    )
    left, bottom, right, top = transform_bounds("EPSG:4326", crs, *vars(bbox).values())
    left, bottom = (
        math.floor(left / RESOLUTION) * RESOLUTION,
        math.floor(bottom / RESOLUTION) * RESOLUTION,
    )
    width, height = (
        math.ceil((right - left) / RESOLUTION),
        math.ceil((top - bottom) / RESOLUTION),
    )
    transform = from_origin(left, bottom + height * RESOLUTION, RESOLUTION, RESOLUTION)

    def read(href: str) -> np.ndarray:
        with (
            rasterio.open(href) as src,
            WarpedVRT(
                src,
                crs=crs,
                transform=transform,
                width=width,
                height=height,
                resampling=Resampling.bilinear,
            ) as vrt,
        ):
            return vrt.read(1)

    hrefs = [item.assets[band].href for item in items for band in BANDS[host]]
    with ThreadPoolExecutor(max_workers=len(hrefs)) as pool:
        futures = [pool.submit(read, h) for h in hrefs]
        for done, _ in enumerate(as_completed(futures), 1):
            if progress:
                progress(done, len(hrefs))
        stack = [f.result() for f in futures]
    for i, item in enumerate(items):
        if _baseline(item) >= 4:
            # Baseline 4.0 added a +1000 offset; the models were trained on baseline 3.
            n = len(BANDS[host])
            stack[i * n : (i + 1) * n] = [
                (b.astype(np.int32) - 1000).clip(min=0).astype(np.uint16)
                for b in stack[i * n : (i + 1) * n]
            ]

    latest = max(i.datetime for i in items)
    out.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        out,
        "w",
        driver="GTiff",
        width=width,
        height=height,
        count=len(stack),
        dtype="uint16",
        crs=crs,
        transform=transform,
        compress="deflate",
        tiled=True,
        blockxsize=256,
        blockysize=256,
    ) as dst:
        dst.write(np.stack(stack))
        dst.update_tags(TIFFTAG_DATETIME=latest.strftime("%Y:%m:%d %H:%M:%S"))
    return {
        "path": str(out),
        "width": width,
        "height": height,
        "crs": crs,
        "scenes": [s.id for s in scenes],
    }


def _day_of_year(url: str, bbox: BBox) -> int:
    with rasterio.open(url) as src:
        window = from_bounds(*vars(bbox).values(), transform=src.transform)
        values = src.read(
            1,
            window=window.round_offsets().round_lengths(),
            boundless=True,
            masked=True,
        )
        if values.count() == 0:  # bbox smaller than a pixel: sample its center
            row, col = src.index(
                (bbox.xmin + bbox.xmax) / 2, (bbox.ymin + bbox.ymax) / 2
            )
            values = src.read(1, window=((row, row + 1), (col, col + 1)), masked=True)
    days = values.compressed()
    days = days[(days >= 1) & (days <= 366)]
    if days.size == 0:
        raise LookupError(f"the crop calendar has no growing season for {bbox}")
    # Circular mean, so a season around new year averages to ~day 1, not ~day 183.
    angles = 2 * np.pi * (days - 1) / 365
    mean = math.atan2(np.sin(angles).mean(), np.cos(angles).mean()) % (2 * np.pi)
    return round(mean * 365 / (2 * np.pi)) + 1


def _from_day(year: int, day: int) -> date:
    return date(year, 1, 1) + timedelta(days=min(day, 365) - 1)


def _aoi_nodata(con: duckdb.DuckDBPyConnection, footprint: dict, bbox: BBox) -> float:
    """Percent of ``bbox`` outside the scene's footprint (planar in degrees, fine at AOI scale)."""
    import json

    covered = con.execute(
        f"""select ST_Area(ST_Intersection(ST_GeomFromGeoJSON(?), {bbox.envelope()}))
                   / ST_Area({bbox.envelope()})""",
        [json.dumps(footprint)],
    ).fetchone()[0]
    return round(100 * (1 - (covered or 0)), 1)


def _baseline(item) -> float:
    value = item.properties.get(
        "processing:version", item.properties.get("s2:processing_baseline", 0)
    )
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
