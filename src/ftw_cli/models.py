"""Released FTW models in ONNX, pinned to a Hugging Face commit and checked by sha256."""

import hashlib
import os
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ftw_cli.progress import Progress

PRUE_CCBY = "https://huggingface.co/fieldsoftheworld/ftw-prue-ccby/resolve/ffe4452768e1054b1d7c2ab39b84b4170f90c7cf/"
FTW_Q = "https://huggingface.co/fieldsoftheworld/ftw-q/resolve/f5aecca0ecd789ccdaf4a9844e2ed0a600a358ee/"


@dataclass(frozen=True)
class Model:
    url: str
    sha256: str
    description: str
    license: str
    # Imagery is divided by this before inference.
    normalization: float = 3000.0
    # Upsampling applied before the model; FTW's Sentinel-2 models expect 2 (10 m to 5 m).
    resize_factor: int = 2
    # "scenes": two Sentinel-2 L2A scenes from the crop calendar. "mosaics": the year's four
    # quarterly cloudless mosaics, tile by tile, as the 2nd Edition was made.
    imagery: Literal["scenes", "mosaics"] = "scenes"


ModelName = Literal["ftw-q", "prue-ccby-b3", "prue-ccby-b5", "prue-ccby-b7"]
MODELS: dict[ModelName, Model] = {
    "ftw-q": Model(
        FTW_Q + "ftw-q-unet-efb3.onnx",
        "2a24bb9207fce358fc9305fab4e8121fea87ce14fd91ee8644cd5069bf4ceaf4",
        "U-Net, EfficientNet-B3, on a year of quarterly mosaics, tiled as the 2nd Edition.",
        "CC-BY-4.0",
        resize_factor=4,
        imagery="mosaics",
    ),
    "prue-ccby-b3": Model(
        PRUE_CCBY + "prue_efnetb3_ccby-b35d59a6.onnx",
        "37f8d087147bb08a2c9ef5c46e7ec4d720d740aa24ccef2eee242243149dd5df",
        "PRUE U-Net, EfficientNet-B3, trained on CC-BY/CC0 countries. Fastest.",
        "CC-BY-4.0",
    ),
    "prue-ccby-b5": Model(
        PRUE_CCBY + "prue_efnetb5_ccby-4fd92b24.onnx",
        "592b619cfcdd3c87765de3cb7ef32d737334cd9c3ac0c2fdb33cb7025e63e0e2",
        "PRUE U-Net, EfficientNet-B5, trained on CC-BY/CC0 countries.",
        "CC-BY-4.0",
    ),
    "prue-ccby-b7": Model(
        PRUE_CCBY + "prue_efnetb7_ccby-4bfbbbde.onnx",
        "fd6798f82b84d3a74338a7091ca98723e4b2bf0221cf7a8eaa8a779908345f3b",
        "PRUE U-Net, EfficientNet-B7, trained on CC-BY/CC0 countries. Most accurate.",
        "CC-BY-4.0",
    ),
}
DEFAULT_MODEL: ModelName = "ftw-q"


def cache_dir() -> Path:
    return Path(os.environ.get("FTW_CACHE_DIR", Path.home() / ".cache" / "ftw-cli"))


def cached(name: ModelName) -> Path | None:
    """The local copy of model ``name``, if it has been downloaded."""
    path = _path(name)
    return path if path.exists() else None


def fetch(name: ModelName, progress: Progress | None = None) -> Path:
    """Local path to model ``name``, downloading and verifying it on first use."""
    path = _path(name)
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    # Download beside the target and rename, so a concurrent reader never sees a partial file.
    with tempfile.NamedTemporaryFile(
        dir=path.parent, suffix=".partial", delete=False
    ) as tmp:
        digest = hashlib.sha256()
        with urllib.request.urlopen(MODELS[name].url) as response:
            total = int(response.headers.get("Content-Length") or 0) or None
            while chunk := response.read(1 << 20):
                digest.update(chunk)
                tmp.write(chunk)
                if progress:
                    progress(tmp.tell(), total)
    if digest.hexdigest() != MODELS[name].sha256:
        os.remove(tmp.name)
        raise OSError(f"{name}: downloaded file does not match its sha256")
    os.replace(tmp.name, path)
    return path


def _path(name: ModelName) -> Path:
    return cache_dir() / "models" / MODELS[name].url.rsplit("/", 1)[-1]
