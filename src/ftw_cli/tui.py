"""``ftw tui``: browse the FTW field boundaries interactively."""

from pathlib import Path
from typing import ClassVar

import duckdb
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.theme import Theme
from textual.widgets import Button, DataTable, Footer, Input, Label, Select, Static
from textual_image.renderable import Image as AutoRenderable
from textual_image.renderable.unicode import Image as UnicodeRenderable
from textual_image.widget import HalfcellImage
from textual_image.widget import Image as AutoImage

from ftw_cli import ftw2e, render

# Without a terminal, textual-image falls back to grayscale characters; colored
# half-blocks show the score ramp, so prefer them there.
MapImage = HalfcellImage if AutoRenderable is UnicodeRenderable else AutoImage

YEARS = range(2025, 2016, -1)
PREVIEW_LIMIT = 20_000
TABLE_ROWS = 500
PRESETS = {
    "Ames, Iowa, USA": "-93.70,41.95,-93.60,42.05",
    "Beauce, France": "1.55,48.20,1.70,48.30",
    "Poltava, Ukraine": "34.40,49.50,34.55,49.60",
    "Mato Grosso, Brazil": "-55.95,-13.10,-55.80,-13.00",
    "Eastern Province, Rwanda": "30.30,-2.05,30.40,-1.95",
}

FTW_THEME = Theme(
    name="ftw",
    primary="#22A070",  # Mountain Meadow
    secondary="#7EC185",  # De York
    accent="#C0D85B",  # Yellow Green
    warning="#FF7F41",  # Burning Orange
    error="#FF7F41",
    success="#7EC185",
    foreground="#FFFFFF",
    background="#004747",  # Cyprus
    surface="#00595A",
    panel="#003838",
    dark=True,
)


def score_legend() -> Text:
    legend = Text("score 0 ")
    for score in range(0, 101, 7):
        legend.append(
            "█", style="#{:02x}{:02x}{:02x}".format(*render.score_color(score))
        )
    return legend.append(" 100")


class FtwApp(App):
    TITLE = "Fields of The World"
    CSS = """
    #brand { background: $accent; color: $background; text-style: bold; padding: 0 1; }
    #sidebar { width: 36; background: $surface; padding: 1 2; }
    #sidebar Select, #sidebar Input, #actions { width: 100%; }
    #main { padding: 0 1; }
    .section { color: $accent; text-style: bold; margin-top: 1; }
    #actions { height: auto; margin-top: 1; }
    #actions Button { width: 1fr; min-width: 0; }
    #summary { margin-top: 1; height: auto; }
    #legend { margin-top: 1; color: $secondary; }
    #mapbox { height: 2fr; align: center middle; border: round $primary; border-title-color: $accent; }
    #map { width: auto; height: auto; }
    #fields { height: 1fr; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("r", "query", "Query"),
        Binding("e", "export", "Export"),
        Binding("q", "quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        first = next(iter(PRESETS))
        yield Static(
            "ftw  Fields of The World  ·  mapping the world's agricultural fields at scale",
            id="brand",
        )
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Label("Area", classes="section")
                yield Select(
                    [(n, b) for n, b in PRESETS.items()],
                    value=PRESETS[first],
                    allow_blank=False,
                    id="preset",
                )
                yield Input(
                    PRESETS[first], placeholder="xmin,ymin,xmax,ymax", id="bbox"
                )
                yield Label("Year", classes="section")
                yield Select(
                    [(str(y), y) for y in YEARS],
                    value=YEARS[0],
                    allow_blank=False,
                    id="year",
                )
                with Horizontal(id="actions"):
                    yield Button("Query", variant="primary", id="query")
                    yield Button("Export", id="export")
                yield Label("Summary", classes="section")
                yield Static("Press r or Query.", id="summary")
                yield Static(score_legend(), id="legend")
            with Vertical(id="main"):
                with Vertical(id="mapbox"):
                    yield MapImage(id="map")
                yield DataTable(cursor_type="row", zebra_stripes=True, id="fields")
        yield Footer()

    def on_mount(self) -> None:
        self.register_theme(FTW_THEME)
        self.theme = "ftw"
        self.query_one("#fields", DataTable).add_columns("id", "area (ha)", "score")
        self.action_query()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "preset":
            self.query_one("#bbox", Input).value = str(event.value)
            self.action_query()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.action_query()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "query":
            self.action_query()
        elif event.button.id == "export":
            self.action_export()

    def _request(self) -> tuple[ftw2e.BBox, int] | None:
        try:
            bbox = ftw2e.BBox.parse(self.query_one("#bbox", Input).value)
        except ValueError as e:
            self.notify(str(e), severity="error")
            return None
        return bbox, self.query_one("#year", Select).value

    def action_query(self) -> None:
        if request := self._request():
            self.query_one("#main").loading = True
            self.run_query(*request)

    @work(thread=True, exclusive=True, group="query")
    def run_query(self, bbox: ftw2e.BBox, year: int) -> None:
        try:
            con = ftw2e.connect()
            summary = ftw2e.summarize(con, bbox, year)
            fields = ftw2e.fields(con, bbox, year, PREVIEW_LIMIT)
            image = render.render(fields, bbox)
        except (duckdb.Error, OSError) as e:
            self.call_from_thread(self._failed, e)
            return
        self.call_from_thread(self._show, summary, fields, image)

    def _failed(self, error: Exception) -> None:
        self.query_one("#main").loading = False
        self.notify(f"Query failed: {error}", severity="error")

    def _show(self, summary: dict, fields: list[dict], image) -> None:
        self.query_one("#main").loading = False
        self.query_one("#map", MapImage).image = image
        self.query_one(
            "#mapbox"
        ).border_title = f"{summary['year']}  ·  {summary['fields']:,} fields"
        score = "–" if summary["mean_score"] is None else f"{summary['mean_score']:.0f}"
        shown = (
            f"\n[dim]map shows the largest {len(fields):,}[/dim]"
            if len(fields) < summary["fields"]
            else ""
        )
        self.query_one("#summary", Static).update(
            f"[b]{summary['fields']:,}[/b] fields\n"
            f"[b]{summary['area_ha']:,.0f}[/b] ha\n"
            f"mean score [b]{score}[/b]\n"
            f"{summary['files']} file(s) read{shown}"
        )
        table = self.query_one("#fields", DataTable)
        table.clear()
        for f in fields[:TABLE_ROWS]:
            table.add_row(f["id"], f"{f['area_ha']:,.2f}", str(f["score"]))

    def action_export(self) -> None:
        if request := self._request():
            self.notify("Exporting…")
            self.run_export(*request)

    @work(thread=True, group="export")
    def run_export(self, bbox: ftw2e.BBox, year: int) -> None:
        corners = "_".join(f"{v:g}" for v in vars(bbox).values())
        out = Path(f"ftw-{year}-{corners}.parquet")
        try:
            ftw2e.export(ftw2e.connect(), bbox, year, out, limit=None)
        except (duckdb.Error, OSError, LookupError, ValueError) as e:
            self.call_from_thread(self.notify, f"Export failed: {e}", severity="error")
            return
        self.call_from_thread(self.notify, f"Wrote {out.resolve()}")


def main() -> None:
    FtwApp().run()
