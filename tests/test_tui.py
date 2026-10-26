import asyncio
from contextlib import ExitStack
from unittest.mock import patch

import pytest

pytest.importorskip("textual")

from ftw_cli import tui_detect
from ftw_cli.tui import FtwApp

SQUARE = {
    "id": "15TVG_0_0-1",
    "area_ha": 12.5,
    "score": 90,
    "geometry": {
        "type": "Polygon",
        "coordinates": [
            [[-93.68, 41.97], [-93.62, 41.97], [-93.62, 42.03], [-93.68, 41.97]]
        ],
    },
}
DETECTED = SQUARE | {"id": "fields-1", "score": None}
HISTORY = [
    {"year": y, "fields": 100 + y - 2017, "area_ha": 1.0, "mean_score": 70.0}
    for y in range(2017, 2026)
]


def summary(con, bbox, year):
    return {
        "year": year,
        "bbox": [],
        "files": 1,
        "fields": 1,
        "area_ha": 12.5,
        "mean_score": 90.0,
    }


def drive(test, **patches):
    """Run ``test(app, pilot)`` against an app whose queries return SQUARE."""

    async def run():
        with ExitStack() as stack:
            stack.enter_context(patch("ftw_cli.ftw2e.connect"))
            stack.enter_context(patch("ftw_cli.ftw2e.summarize", side_effect=summary))
            stack.enter_context(patch("ftw_cli.ftw2e.fields", return_value=[SQUARE]))
            stack.enter_context(patch("ftw_cli.ftw2e.history", return_value=HISTORY))
            for target, value in patches.items():
                stack.enter_context(patch(target, value))
            app = FtwApp()
            async with app.run_test(size=(140, 44)) as pilot:
                await until(pilot, lambda: app.query_one("#fields").row_count)
                await test(app, pilot)

    asyncio.run(run())


async def until(pilot, condition, tries: int = 50) -> None:
    for _ in range(tries):
        await pilot.pause(0.1)
        if condition():
            return
    raise AssertionError("condition never held")


def test_query_fills_summary_table_map_and_timeline():
    async def test(app, pilot):
        await until(pilot, lambda: app.query_one("#timeline").data)
        assert "1" in str(app.query_one("#summary").render())
        assert app.query_one("#map").image is not None
        assert app.query_one("#mapbox").border_title == "2025  ·  1 fields"
        assert app.query_one("#timeline").data == [r["fields"] for r in HISTORY]
        assert "+8%" in str(app.query_one("#trend").render())

    drive(test)


def test_brackets_step_through_years():
    async def test(app, pilot):
        await pilot.press("left_square_bracket")
        await until(
            pilot, lambda: app.query_one("#mapbox").border_title.startswith("2024")
        )
        assert app.year == 2024
        await pilot.press("right_square_bracket", "right_square_bracket")
        assert app.year == 2025  # 2025 is the last year

    drive(test)


def test_highlighting_a_row_shows_the_field():
    async def test(app, pilot):
        app.query_one("#fields").focus()
        await pilot.press("down", "up")
        await until(
            pilot, lambda: SQUARE["id"] in str(app.query_one("#detail").render())
        )
        assert "12.50" in str(app.query_one("#detail").render())

    drive(test)


def test_detect_without_the_inference_extra_says_how_to_install_it():
    async def test(app, pilot):
        await pilot.press("d")
        await pilot.pause(0.2)
        assert any("ftw-cli[inference]" in str(n.message) for n in app._notifications)
        assert app.detected is None

    drive(test, **{"ftw_cli.tui_detect.missing": lambda: ["onnxruntime"]})


def test_detected_fields_replace_the_map_and_m_switches_back(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def detect(bbox, year, out_dir, model, progress):
        for step in range(len(tui_detect.STEPS)):
            progress(step, 1, 1)
        return [DETECTED]

    async def test(app, pilot):
        await pilot.press("d")
        await until(pilot, lambda: app.detected is not None)
        await until(pilot, lambda: app.source == "model")
        await until(pilot, lambda: "model" in app.query_one("#mapbox").border_subtitle)
        await pilot.press("m")
        await until(pilot, lambda: app.source == "edition")
        await until(
            pilot, lambda: "2nd Edition" in app.query_one("#mapbox").border_subtitle
        )

    drive(
        test,
        **{
            "ftw_cli.tui_detect.missing": list,
            "ftw_cli.tui_detect.detect": detect,
        },
    )
