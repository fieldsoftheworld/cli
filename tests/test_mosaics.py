import duckdb
import numpy as np
import pytest

rasterio = pytest.importorskip("rasterio")
pytest.importorskip("scipy")

from rasterio.transform import from_origin

from ftw_cli import mosaics, quarterly


class Session:
    """Stands in for onnxruntime: logits that vary with the input, so blending shows."""

    class _Input:
        name = "input"

    def get_inputs(self):
        return [self._Input()]

    def run(self, outputs, feed):
        x = feed["input"]  # (b, 16, 512, 512)
        field = x[:, 0] - x[:, 1]
        return [np.stack([np.zeros_like(field), field, x[:, 2] * 3], axis=1)]


def reference(stack: np.ndarray, session) -> np.ndarray:
    """The catalog's algorithm without streaming: one accumulator over the whole output."""
    x = quarterly.fill_nodata(stack).astype(np.float32) / np.float32(3000)
    h, w = x.shape[1:]
    rows, cols = quarterly._starts(h, 96), quarterly._starts(w, 96)
    win = np.maximum(
        np.outer(np.hanning(512), np.hanning(512)).astype(np.float32), 1e-3
    )
    acc = np.zeros((2, h * 4, w * 4), np.float32)
    wsum = np.zeros((h * 4, w * 4), np.float32)
    for r in rows:
        for c in cols:
            patch = quarterly._upsample(x[None, :, r : r + 128, c : c + 128])
            prob = quarterly._softmax(session.run(None, {"input": patch})[0])[0, 1:3]
            acc[:, r * 4 : r * 4 + 512, c * 4 : c * 4 + 512] += prob * win
            wsum[r * 4 : r * 4 + 512, c * 4 : c * 4 + 512] += win
    return quarterly._to_uint8(acc / wsum)


def test_streamed_blending_matches_a_whole_image_accumulator():
    rng = np.random.default_rng(0)
    # 300 rows: patch rows start at 0, 96, and a last one at 172, closer than a stride.
    stack = rng.integers(0, 3000, (16, 300, 230), dtype=np.int16)
    out = quarterly.predict(stack, Session(), batch=3)
    assert out.shape == (2, 1200, 920)
    # numpy's vectorized exp can round a last bit differently by batch, which moves a
    # handful of pixels by 1/255; a streaming mistake would move whole rows.
    diff = np.abs(out.astype(int) - reference(stack, Session()).astype(int))
    assert diff.max() <= 1 and (diff > 0).mean() < 1e-5


def test_small_windows_are_padded_like_the_catalog():
    stack = np.full((16, 90, 100), 1500, dtype=np.int16)
    assert quarterly.predict(stack, Session()).shape == (2, 360, 400)


def test_fill_nodata_cases():
    x = np.full((16, 60, 60), 1000, dtype=np.int16)
    assert quarterly.fill_nodata(x) is x  # nothing to fill
    x[0, 30, 30] = quarterly.NODATA  # one pixel: the window mean of its neighbors
    x[4, :, :] = quarterly.NODATA  # a whole quarter of band 0: the other quarters' mean
    x[8, :, :] = 2000
    x[8, :, :][x[4] == quarterly.NODATA] = 2000
    filled = quarterly.fill_nodata(x)
    assert filled[0, 30, 30] == 1000
    others = np.ones((60, 60), bool)
    others[30, 30] = False  # quarter 1 is missing there too: the mean of 2000 and 1000
    assert (filled[4][others] == round((1000 + 2000 + 1000) / 3)).all()
    assert filled[4, 30, 30] == 1500
    assert (filled != quarterly.NODATA).all()


def test_square_and_zone_follow_the_2nd_edition():
    tile = mosaics.Tile(
        "15TVG_0_0", "EPSG:32615", from_origin(399960, 4700040, 10, 10), 10008, 10008
    )
    assert mosaics._square(tile.transform) == (400000.0, 4600000.0, 500000.0, 4700000.0)
    assert mosaics._lon_range(tile) == (-96.0, -90.0)
    norway = mosaics.Tile("32VKM_0_0", "EPSG:32632", tile.transform, 1, 1)
    assert mosaics._lon_range(norway) == (3.0, 12.0)  # 32V is widened west to 3E


def test_owned_keeps_fields_centered_in_the_square_and_zone():
    tile = mosaics.Tile(
        "15TVG_0_0", "EPSG:32615", from_origin(399960, 4700040, 10, 10), 10008, 10008
    )
    part = mosaics.Part(
        tile,
        2024,
        rasterio.windows.Window(0, 0, 10, 10),
        tile.transform,
        tile.crs,
        mosaics._square(tile.transform),
        mosaics._lon_range(tile),
    )
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial; SET geometry_always_xy = true")
    kept = con.execute(
        f"""
        select name from (values
            ('inside', ST_MakeEnvelope(450000, 4650000, 450100, 4650100)),
            ('west of the square', ST_MakeEnvelope(399970, 4650000, 399990, 4650100)),
            ('north of the square', ST_MakeEnvelope(450000, 4700010, 450100, 4700030))
        ) t(name, geom) where {mosaics.owned(part)}
        """
    ).fetchall()
    assert kept == [("inside",)]


def test_kept_scores_are_reused_only_for_the_same_window_and_model(tmp_path):
    tile = mosaics.Tile(
        "15TVG_0_0", "EPSG:32615", from_origin(399960, 4700040, 10, 10), 10008, 10008
    )
    part = mosaics.Part(
        tile,
        2024,
        rasterio.windows.Window(5, 5, 130, 140),
        tile.transform,
        tile.crs,
        (0, 0, 1, 1),
        (0, 1),
    )
    scores = np.random.default_rng(1).integers(0, 255, (2, 560, 520), dtype=np.uint8)
    path = tmp_path / "15TVG_0_0.tif"
    mosaics.save(path, scores, part, "ftw-q")

    reused = mosaics.kept(path, part, "ftw-q")
    assert reused is not None and np.array_equal(reused, scores)
    assert mosaics.kept(path, part, "other-model") is None
    moved = mosaics.Part(
        tile,
        2024,
        rasterio.windows.Window(6, 5, 130, 140),
        *list(vars(part).values())[3:],
    )
    assert mosaics.kept(path, moved, "ftw-q") is None
    assert mosaics.kept(tmp_path / "missing.tif", part, "ftw-q") is None
