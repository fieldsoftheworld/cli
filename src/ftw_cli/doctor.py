"""``ftw doctor``: what is installed and what is reachable, for bug reports."""

import platform
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import version

import duckdb

from ftw_cli import USER_AGENT, __version__, areas, ftw2e, models, vectors
from ftw_cli.cli_args import missing


class Missing(Exception):
    """An optional extra is not installed."""


def ftw_cli() -> str:
    return f"{__version__}, Python {platform.python_version()}, {platform.platform()}"


def duckdb_() -> str:
    vectors.connect()
    return f"{duckdb.__version__}, httpfs and spatial load"


def imagery() -> str:
    _need("imagery")
    import rasterio

    return f"rasterio {rasterio.__version__}, GDAL {rasterio.__gdal_version__}"


def inference() -> str:
    _need("inference")
    import onnxruntime

    providers = [
        p.removesuffix("ExecutionProvider")
        for p in onnxruntime.get_available_providers()
    ]
    gpu = "GPU" if "CUDA" in providers else "CPU only"
    return f"onnxruntime {onnxruntime.__version__} ({gpu}: {', '.join(providers)}), ftw-fbp {version('ftw-fbp')}"


def stats() -> str:
    _need("stats")
    return f"exactextract {version('exactextract')}"


def cache() -> str:
    root = models.cache_dir()
    size = sum(f.stat().st_size for f in root.rglob("*") if f.is_file())
    cached = [name for name in models.MODELS if models.cached(name)]
    return f"{root}, {size / 1e6:,.0f} MB, models: {', '.join(cached) or 'none'}"


CHECKS: dict[str, Callable[[], str]] = {
    "ftw-cli": ftw_cli,
    "duckdb": duckdb_,
    "imagery extra": imagery,
    "inference extra": inference,
    "stats extra": stats,
    "cache": cache,
    "Source Coop": lambda: _reach(ftw2e.VECTOR_INDEX),
    "Planetary Computer": lambda: _reach(
        "https://planetarycomputer.microsoft.com/api/stac/v1"
    ),
    "Hugging Face": lambda: _reach(models.MODELS[models.DEFAULT_MODEL].url),
    "Nominatim": lambda: _reach(areas.SEARCH_URL.replace("/search", "/status")),
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


def _need(extra: str) -> None:
    if gone := missing(extra):
        raise Missing(f"missing {', '.join(gone)}: pip install 'ftw-cli[{extra}]'")


def _reach(url: str) -> str:
    request = urllib.request.Request(
        url, headers={"Range": "bytes=0-0", "User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return f"HTTP {response.status} from {url.split('/')[2]}"
