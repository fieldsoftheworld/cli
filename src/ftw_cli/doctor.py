"""``ftw doctor``: what is installed and what is reachable, for bug reports."""

import importlib.util
import platform
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import version

import duckdb

from ftw_cli import __version__, ftw2e, models, places


class Missing(Exception):
    """An optional extra is not installed."""


def ftw_cli() -> str:
    return f"{__version__}, Python {platform.python_version()}, {platform.platform()}"


def duckdb_() -> str:
    ftw2e.connect()
    return f"{duckdb.__version__}, httpfs and spatial load"


def imagery() -> str:
    _need("rasterio", "pystac_client", "planetary_computer", extra="imagery")
    import rasterio

    return f"rasterio {rasterio.__version__}, GDAL {rasterio.__gdal_version__}"


def inference() -> str:
    _need("onnxruntime", "fbp", extra="inference")
    import onnxruntime

    providers = [
        p.removesuffix("ExecutionProvider")
        for p in onnxruntime.get_available_providers()
    ]
    gpu = "GPU" if "CUDA" in providers else "CPU only"
    return f"onnxruntime {onnxruntime.__version__} ({gpu}: {', '.join(providers)}), ftw-fbp {version('ftw-fbp')}"


def cache() -> str:
    root = models.cache_dir()
    size = (
        sum(f.stat().st_size for f in root.rglob("*") if f.is_file())
        if root.exists()
        else 0
    )
    cached = [name for name in models.MODELS if models.cached(name)]
    return f"{root}, {size / 1e6:,.0f} MB, models: {', '.join(cached) or 'none'}"


def source_coop() -> str:
    return _reach(ftw2e.VECTOR_INDEX)


def planetary_computer() -> str:
    return _reach("https://planetarycomputer.microsoft.com/api/stac/v1")


def hugging_face() -> str:
    return _reach(models.MODELS[models.DEFAULT_MODEL].url)


def nominatim() -> str:
    return _reach(places.SEARCH_URL.replace("/search", "/status"))


CHECKS: dict[str, Callable[[], str]] = {
    "ftw-cli": ftw_cli,
    "duckdb": duckdb_,
    "imagery extra": imagery,
    "inference extra": inference,
    "cache": cache,
    "Source Coop": source_coop,
    "Planetary Computer": planetary_computer,
    "Hugging Face": hugging_face,
    "Nominatim": nominatim,
}


def run() -> list[dict]:
    """Every check at once. A check fails by raising; ``Missing`` means not installed."""
    with ThreadPoolExecutor(max_workers=len(CHECKS)) as pool:
        futures = {name: pool.submit(check) for name, check in CHECKS.items()}
    rows = []
    for name, future in futures.items():
        error = future.exception()
        status = (
            "missing" if isinstance(error, Missing) else "failed" if error else "ok"
        )
        rows.append(
            {"check": name, "status": status, "detail": str(error or future.result())}
        )
    return rows


def _need(*modules: str, extra: str) -> None:
    if missing := [m for m in modules if importlib.util.find_spec(m) is None]:
        raise Missing(f"missing {', '.join(missing)}: pip install 'ftw-cli[{extra}]'")


def _reach(url: str) -> str:
    request = urllib.request.Request(
        url, headers={"Range": "bytes=0-0", "User-Agent": places.USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return f"HTTP {response.status} from {url.split('/')[2]}"
