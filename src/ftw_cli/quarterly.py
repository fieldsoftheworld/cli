"""Inference on quarterly mosaics, as the FTW 2nd Edition ran it, in numpy and onnxruntime.

A port of ftw-global-data-catalog's ``pipeline/inference`` (``predict.py``, ``nodata.py``):
the 16-band Q1-Q4 x B04/B03/B02/B08 stack has its nodata filled (``window31``), is divided
by 3000 and upsampled x4 with bilinear interpolation, and 512 px patches with 25% overlap
are blended with a Hann window into uint8 field and boundary probabilities at 2.5 m.
"""

from collections.abc import Callable

import numpy as np
from scipy.ndimage import uniform_filter

PATCH, UP = 512, 4
NATIVE = PATCH // UP
NODATA = -32768
FILL_WINDOW, FILL_MIN_VALID = 31, 0.30


def fill_nodata(x: np.ndarray) -> np.ndarray:
    """Fill mosaic nodata in a (16, H, W) stack, channel = quarter * 4 + band.

    Each nodata pixel gets the mean of the same quarter and band over a 31 px window when
    at least 30% of it is valid (borders count as invalid), else the mean of the other
    quarters' valid values there, else the median of that quarter and band.
    """
    bad = x == NODATA
    has = bad.reshape(16, -1).any(1)
    if not has.any():
        return x
    y = x.copy()
    for band in range(4):
        chans = [q * 4 + band for q in range(4)]
        if not has[chans].any():
            continue
        valid = ~bad[chans]
        xb = np.where(valid, x[chans].astype(np.float32), np.float32(0))
        total, count = xb.sum(0), valid.sum(0, dtype=np.int32)
        for q, c in enumerate(chans):
            if not has[c]:
                continue
            den = uniform_filter(
                valid[q].astype(np.float32), FILL_WINDOW, mode="constant"
            )
            num = uniform_filter(xb[q], FILL_WINDOW, mode="constant")
            others = count - valid[q]
            fill = np.where(
                den >= FILL_MIN_VALID,
                num / np.maximum(den, 1e-6),
                (total - xb[q]) / np.maximum(others, 1),
            )
            median = bad[c] & (den < FILL_MIN_VALID) & (others == 0)
            if median.any():
                vals = np.sort(xb[q][valid[q]])
                # torch's median, which the catalog uses: the lower middle value, not the mean.
                fill[median] = vals[(len(vals) - 1) // 2] if len(vals) else 0
            info = np.iinfo(x.dtype)
            fill = np.clip(np.rint(fill), info.min + 1, info.max)
            y[c] = np.where(bad[c], fill.astype(x.dtype), x[c])
    return y


def predict(
    stack: np.ndarray,
    session,
    batch: int = 16,
    progress: Callable[[int, int | None], None] | None = None,
) -> np.ndarray:
    """(16, H, W) int16 mosaics -> (2, 4H, 4W) uint8 field and boundary probabilities.

    Patches go through the model one row of patches at a time, and only the output rows
    those patches still cover are held as floats, so memory stays near one patch row of
    the full width however large the window is.
    """
    x = fill_nodata(stack).astype(np.float32) / np.float32(3000)
    h, w = x.shape[1:]
    if h < NATIVE or w < NATIVE:  # the catalog pads small tiles by repeating the edge
        x = np.pad(
            x, ((0, 0), (0, max(0, NATIVE - h)), (0, max(0, NATIVE - w))), "edge"
        )
    H, W = x.shape[1:]
    stride = round(NATIVE * 0.75)
    rows, cols = _starts(H, stride), _starts(W, stride)
    window = np.maximum(np.outer(*[np.hanning(PATCH).astype(np.float32)] * 2), 1e-3)
    out = np.zeros((2, H * UP, W * UP), np.uint8)
    acc = np.zeros((2, PATCH + stride * UP, W * UP), np.float32)
    weight = np.zeros(acc.shape[1:], np.float32)
    name = session.get_inputs()[0].name
    for i, r in enumerate(rows):
        patches = [x[:, r : r + NATIVE, c : c + NATIVE] for c in cols]
        for b in range(0, len(patches), batch):
            logits = session.run(
                None, {name: _upsample(np.stack(patches[b : b + batch]))}
            )[0]
            probs = _softmax(np.asarray(logits))[:, 1:3]
            for c, prob in zip(cols[b : b + batch], probs, strict=True):
                acc[:, :PATCH, c * UP : c * UP + PATCH] += prob * window
                weight[:PATCH, c * UP : c * UP + PATCH] += window
        # Rows above the next patch row get no more patches: finish them and shift up.
        done = (rows[i + 1] - r) * UP if i + 1 < len(rows) else PATCH
        top = r * UP
        out[:, top : top + done] = _to_uint8(acc[:, :done] / weight[:done])
        acc = np.concatenate([acc[:, done:], np.zeros_like(acc[:, :done])], axis=1)
        weight = np.concatenate([weight[done:], np.zeros_like(weight[:done])])
        if progress:
            progress(i + 1, len(rows))
    return out[:, : h * UP, : w * UP]


def _starts(extent: int, stride: int) -> list[int]:
    starts = list(range(0, max(extent - NATIVE, 0) + 1, stride))
    return sorted({*starts, max(extent - NATIVE, 0)})


def _upsample(x: np.ndarray) -> np.ndarray:
    """Bilinear x4 with half-pixel centers (PyTorch's ``align_corners=False``)."""

    def axis(n: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        src = np.maximum((np.arange(n * UP) + 0.5) / UP - 0.5, 0)
        i0 = np.floor(src).astype(int)
        return i0, np.minimum(i0 + 1, n - 1), (src - i0).astype(np.float32)

    (r0, r1, rt), (c0, c1, ct) = axis(x.shape[2]), axis(x.shape[3])
    # Columns, then rows: the order of PyTorch's kernel, so the floats round the same way.
    cols = x[..., c0] * (1 - ct) + x[..., c1] * ct
    return np.ascontiguousarray(
        cols[:, :, r0] * (1 - rt)[:, None] + cols[:, :, r1] * rt[:, None]
    )


def _softmax(logits: np.ndarray) -> np.ndarray:
    e = np.exp(logits - logits.max(axis=1, keepdims=True))
    return e / e.sum(axis=1, keepdims=True)


def _to_uint8(p: np.ndarray) -> np.ndarray:
    return np.rint(np.clip(p, 0, 1) * 255).astype(np.uint8)
