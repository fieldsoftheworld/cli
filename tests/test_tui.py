import asyncio
from unittest.mock import patch

import pytest

pytest.importorskip("textual")

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


def test_query_fills_summary_table_and_map():
    summary = {
        "year": 2025,
        "bbox": [],
        "files": 1,
        "fields": 1,
        "area_ha": 12.5,
        "mean_score": 90.0,
    }

    async def run():
        with (
            patch("ftw_cli.ftw2e.connect"),
            patch("ftw_cli.ftw2e.summarize", return_value=summary),
            patch("ftw_cli.ftw2e.fields", return_value=[SQUARE]),
        ):
            app = FtwApp()
            async with app.run_test(size=(120, 40)) as pilot:
                for _ in range(50):
                    await pilot.pause(0.1)
                    if app.query_one("#fields").row_count:
                        break
                assert "1" in str(app.query_one("#summary").render())
                assert app.query_one("#fields").row_count == 1
                assert app.query_one("#map").image is not None
                assert app.query_one("#mapbox").border_title == "2025  ·  1 fields"

    asyncio.run(run())
