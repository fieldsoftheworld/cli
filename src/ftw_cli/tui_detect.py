"""The TUI's "detect fields here": run a model on the shown area and load its fields."""

import importlib.util
import json
from collections.abc import Callable
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.containers import Grid, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, ProgressBar, Static
from textual.worker import get_current_worker

from ftw_cli import ftw2e
from ftw_cli.ftw2e import BBox

STEPS = (
    "Find scenes on the crop calendar",
    "Read Sentinel-2 bands",
    "Download the model",
    "Run the model",
    "Split fields with BoundaryVote",
)
# The modules each step imports; missing ones mean the inference extra is not installed.
NEEDS = ("rasterio", "pystac_client", "onnxruntime", "fbp")

Progress = Callable[[int, int, int | None], None]


def missing() -> list[str]:
    return [m for m in NEEDS if importlib.util.find_spec(m) is None]


def detect(
    bbox: BBox, year: int, out_dir: Path, model: str, progress: Progress
) -> list[dict]:
    """Download imagery for ``bbox``, run ``model``, and return the fields it finds."""
    from ftw_cli import imagery, infer, models

    out_dir.mkdir(parents=True, exist_ok=True)
    progress(0, 0, None)
    start, end = imagery.season(bbox, year)
    scenes = [imagery.search(bbox, when, "mspc", 14, 20) for when in (start, end)]
    progress(0, 1, 1)
    imagery.download(
        scenes, bbox, out_dir / "imagery.tif", "mspc", lambda d, t: progress(1, d, t)
    )
    models.fetch(model, lambda d, t: progress(2, d, t))
    progress(2, 1, 1)
    infer.run(
        out_dir / "imagery.tif",
        out_dir / "scores.tif",
        model,
        progress=lambda d, t: progress(3, d, t),
    )
    progress(4, 0, None)
    infer.polygonize(out_dir / "scores.tif", out_dir / "fields.parquet", simplify=0)
    progress(4, 1, 1)
    return load(out_dir / "fields.parquet")


def load(path: Path) -> list[dict]:
    """Fields from an ``ftw infer polygonize`` GeoParquet, largest first."""
    con = ftw2e.connect()
    rows = con.execute(
        """
        select id, round("metrics:area" / 1e4, 2) as area_ha,
               ST_AsGeoJSON(geometry) as geometry
        from read_parquet(?) order by "metrics:area" desc
        """,
        [str(path)],
    ).fetchall()
    return [
        {"id": i, "area_ha": a, "score": None, "geometry": json.loads(g)}
        for i, a, g in rows
    ]


class DetectScreen(ModalScreen[list[dict] | None]):
    """Live progress for ``detect``; dismissed with the fields, or None on failure."""

    DEFAULT_CSS = """
    DetectScreen { align: center middle; }
    #dialog { width: 72; height: auto; padding: 1 2; background: $surface;
              border: round $accent; border-title-color: $accent; }
    #steps { grid-size: 2; grid-columns: 1fr 24; grid-rows: 1; height: auto; margin: 1 0; }
    #steps ProgressBar Bar { width: 16; }
    #status { color: $secondary; height: auto; }
    #cancel { margin-top: 1; width: 100%; }
    """
    BINDINGS = [("escape", "cancel", "Cancel")]  # noqa: RUF012

    def __init__(self, bbox: BBox, year: int, out_dir: Path, model: str) -> None:
        super().__init__()
        self.bbox, self.year, self.out_dir, self.model = bbox, year, out_dir, model

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog") as dialog:
            dialog.border_title = f"Detect fields · {self.model} · {self.year}"
            with Grid(id="steps"):
                for i, step in enumerate(STEPS):
                    yield Label(step)
                    yield ProgressBar(id=f"step{i}", show_eta=False)
            yield Static(f"Writing to {self.out_dir}", id="status")
            yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.run_detect()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.action_cancel()

    def action_cancel(self) -> None:
        self.workers.cancel_all()
        self.dismiss(None)

    @work(thread=True, exclusive=True)
    def run_detect(self) -> None:
        worker = get_current_worker()

        def progress(step: int, done: int, total: int | None) -> None:
            if not worker.is_cancelled:
                self.app.call_from_thread(self._advance, step, done, total)

        try:
            fields = detect(self.bbox, self.year, self.out_dir, self.model, progress)
        except Exception as e:  # noqa: BLE001  (any failure is shown, not raised in a thread)
            if not worker.is_cancelled:
                self.app.call_from_thread(self._failed, e)
            return
        if not worker.is_cancelled:
            self.app.call_from_thread(self.dismiss, fields)

    def _advance(self, step: int, done: int, total: int | None) -> None:
        self.query_one(f"#step{step}", ProgressBar).update(total=total, progress=done)

    def _failed(self, error: Exception) -> None:
        self.query_one("#status", Static).update(f"[b red]Failed:[/] {error}")
        self.query_one("#cancel", Button).label = "Close"
