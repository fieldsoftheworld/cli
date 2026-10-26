"""``ftw tui``: browse the FTW field boundaries interactively."""

from collections.abc import Iterable
from pathlib import Path
from typing import ClassVar

import duckdb
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.theme import Theme
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Input,
    Label,
    Select,
    Sparkline,
    Static,
)
from textual_image.renderable import Image as AutoRenderable
from textual_image.renderable.unicode import Image as UnicodeRenderable
from textual_image.widget import HalfcellImage
from textual_image.widget import Image as AutoImage

from ftw_cli import ftw2e, models, render, tui_detect

# Without a terminal, textual-image falls back to grayscale characters; colored
# half-blocks show the score ramp, so prefer them there.
MapImage = HalfcellImage if AutoRenderable is UnicodeRenderable else AutoImage

YEARS = range(2025, 2016, -1)
PREVIEW_LIMIT = 20_000
TABLE_ROWS = 500
EXPORTS = (".parquet", ".geojson", ".gpkg", ".fgb")
MODEL_FILL = (181, 225, 230)  # Powder Blue, so model fields read apart from the edition
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


def year_strip(year: int) -> Text:
    """The years as two digits, with the shown one marked."""
    strip = Text()
    for y in sorted(YEARS):
        style = "bold #004747 on #C0D85B" if y == year else "#7EC185"
        strip.append(f"{y % 100:02d}", style=style).append(" ")
    return strip


class FtwApp(App):
    TITLE = "Fields of The World"
    CSS = """
    #brand { background: $accent; color: $background; text-style: bold; padding: 0 1; }
    #sidebar { width: 38; background: $surface; padding: 0 2; }
    #sidebar Select, #sidebar Input, #actions { width: 100%; }
    #main { padding: 0 1; }
    .section { color: $accent; text-style: bold; margin-top: 1; }
    #actions { height: auto; margin-top: 1; }
    #actions Button { width: 1fr; min-width: 0; }
    #timeline { height: 3; margin-top: 1; }
    #timeline > .sparkline--max-color { color: $accent; }
    #timeline > .sparkline--min-color { color: $primary; }
    #summary { height: auto; }
    #legend { margin-top: 1; color: $secondary; }
    #mapbox { height: 2fr; align: center middle; border: round $primary;
              border-title-color: $accent; border-subtitle-color: $secondary; }
    #map { width: auto; height: auto; }
    #lower { height: 1fr; }
    #fields { width: 2fr; }
    #detail { width: 1fr; padding: 0 2; border-left: tall $surface; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("r", "query", "Query"),
        Binding("left_square_bracket", "year(-1)", "Year ◀", key_display="["),
        Binding("right_square_bracket", "year(1)", "Year ▶", key_display="]"),
        Binding("d", "detect", "Detect fields"),
        Binding("m", "toggle_source", "2E / model"),
        Binding("e", "export", "Export"),
        Binding("slash", "search", "Search", key_display="/"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.place: str | None = next(iter(PRESETS))
        self.bbox = ftw2e.BBox.parse(PRESETS[self.place])
        self.year = YEARS[0]
        self.edition: list[dict] = []
        self.detected: list[dict] | None = None
        self.source = "edition"
        self.base_image = None
        self.history: dict[ftw2e.BBox, list[dict]] = {}

    def compose(self) -> ComposeResult:
        first = next(iter(PRESETS))
        yield Static(id="brand")
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Label("Place", classes="section")
                yield Input(placeholder="Search a place, then Enter", id="place")
                yield Select(
                    [(n, b) for n, b in PRESETS.items()],
                    value=PRESETS[first],
                    prompt="Presets",
                    id="preset",
                )
                yield Input(
                    PRESETS[first], placeholder="xmin,ymin,xmax,ymax", id="bbox"
                )
                yield Label("Year", classes="section")
                yield Static(year_strip(self.year), id="years")
                yield Sparkline([], id="timeline")
                yield Static("[dim]fields per year, loading…[/dim]", id="trend")
                yield Label("Summary", classes="section")
                yield Static("Press r or Query.", id="summary")
                yield Static(score_legend(), id="legend")
                with Horizontal(id="actions"):
                    yield Button("Query", variant="primary", id="query")
                    yield Button("Detect", id="detect")
                    yield Button("Export", id="export")
            with Vertical(id="main"):
                with Vertical(id="mapbox"):
                    yield MapImage(id="map")
                with Horizontal(id="lower"):
                    yield DataTable(cursor_type="row", zebra_stripes=True, id="fields")
                    yield Static("[dim]Select a field.[/dim]", id="detail")
        yield Footer()

    def on_mount(self) -> None:
        self.register_theme(FTW_THEME)
        self.theme = "ftw"
        table = self.query_one("#fields", DataTable)
        table.add_columns("id", "area (ha)", "score")
        # Focus the table, not the search box, so the single-key bindings work at once.
        table.focus()
        self.action_query()

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        yield from super().get_system_commands(screen)
        for name, box in PRESETS.items():
            yield SystemCommand(f"Go to {name}", box, lambda b=box: self._goto(b))
        for year in YEARS:
            yield SystemCommand(
                f"Show {year}", "Query this year", lambda y=year: self._set_year(y)
            )
        for suffix in EXPORTS:
            yield SystemCommand(
                f"Export as {suffix}",
                "Write the area's fields to the current directory",
                lambda s=suffix: self.action_export(s),
            )
        yield SystemCommand("Detect fields here", "Run a model", self.action_detect)

    # Area and year

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "preset" or event.value is Select.BLANK:
            return
        if ftw2e.BBox.parse(str(event.value)) != self.bbox:
            self._goto(str(event.value))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "place" and event.value.strip():
            self.notify(f"Searching for {event.value}…")
            self.run_geocode(event.value.strip())
        elif event.input.id == "bbox":
            self._goto(event.value)
        self.query_one("#fields", DataTable).focus()

    def bbox_text(self) -> str:
        return ",".join(f"{v:g}" for v in vars(self.bbox).values())

    def _goto(self, text: str, place: str | None = None) -> None:
        try:
            self.bbox = ftw2e.BBox.parse(text)
        except ValueError as e:
            self.notify(str(e), severity="error")
            return
        preset = next((n for n, b in PRESETS.items() if b == text), None)
        self.place = place or preset
        self.query_one("#bbox", Input).value = self.bbox_text()
        # Keep the preset list honest: blank when the area is not one of them.
        self.query_one("#preset", Select).value = (
            PRESETS[preset] if preset else Select.BLANK
        )
        self.detected, self.source = None, "edition"
        self.action_query()

    def _set_year(self, year: int) -> None:
        self.year = year
        self.detected, self.source = None, "edition"
        self.action_query()

    def action_year(self, step: int) -> None:
        if (year := self.year + step) in YEARS:
            self._set_year(year)

    def action_search(self) -> None:
        self.query_one("#place", Input).focus()

    @work(thread=True, exclusive=True, group="geocode")
    def run_geocode(self, query: str) -> None:
        from ftw_cli import places

        try:
            place = places.geocode(query)
        except (LookupError, OSError, ValueError) as e:
            self.call_from_thread(self.notify, f"{e}", severity="error")
            return
        text = ",".join(f"{v:.4f}" for v in vars(place.bbox).values())
        self.call_from_thread(self._goto, text, place.name.split(",")[0])

    # Query and display

    def action_query(self) -> None:
        self._header()
        self.query_one("#years", Static).update(year_strip(self.year))
        self.query_one("#main").loading = True
        self.run_query(self.bbox, self.year)
        if self.bbox not in self.history:
            self.run_history(self.bbox)

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

    @work(thread=True, exclusive=True, group="history")
    def run_history(self, bbox: ftw2e.BBox) -> None:
        try:
            rows = ftw2e.history(bbox)
        except (duckdb.Error, OSError):
            return
        self.call_from_thread(self._show_history, bbox, rows)

    def _failed(self, error: Exception) -> None:
        self.query_one("#main").loading = False
        self.notify(f"Query failed: {error}", severity="error")

    def _header(self) -> None:
        where = self.place or self.bbox_text()
        self.query_one("#brand", Static).update(
            f"ftw  Fields of The World  ·  {where}  ·  {self.year}"
        )

    def _show(self, summary: dict, fields: list[dict], image) -> None:
        self.query_one("#main").loading = False
        self.edition = fields
        score = "–" if summary["mean_score"] is None else f"{summary['mean_score']:.0f}"
        shown = (
            f"\n[dim]map shows the largest {len(fields):,}[/dim]"
            if len(fields) < summary["fields"]
            else ""
        )
        self.query_one("#summary", Static).update(
            f"[b]{summary['fields']:,}[/b] fields  [b]{summary['area_ha']:,.0f}[/b] ha\n"
            f"mean score [b]{score}[/b]  ·  {summary['files']} file(s){shown}"
        )
        self._show_fields(fields, image, f"{summary['fields']:,} fields")

    def _show_fields(self, fields: list[dict], image, count: str) -> None:
        self.base_image = image
        self.query_one("#map", MapImage).image = image
        box = self.query_one("#mapbox")
        source = "2nd Edition" if self.source == "edition" else "model"
        box.border_title = f"{self.year}  ·  {count}"
        box.border_subtitle = f"{source}  ·  m toggles" if self.detected else source
        table = self.query_one("#fields", DataTable)
        table.clear()
        for f in fields[:TABLE_ROWS]:
            score = "" if f["score"] is None else str(f["score"])
            table.add_row(f["id"], f"{f['area_ha']:,.2f}", score, key=f["id"])

    def _show_history(self, bbox: ftw2e.BBox, rows: list[dict]) -> None:
        self.history[bbox] = rows
        if bbox != self.bbox:
            return
        counts = [r["fields"] for r in rows]
        self.query_one("#timeline", Sparkline).data = counts
        change = f"{counts[-1] / counts[0] - 1:+.0%}" if counts[0] else "–"
        self.query_one("#trend", Static).update(
            f"[dim]fields per year, {rows[0]['year']}–{rows[-1]['year']}:[/dim] "
            f"[b]{change}[/b]"
        )

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        fields = self.edition if self.source == "edition" else self.detected or []
        field = next((f for f in fields if f["id"] == event.row_key.value), None)
        if field is None or self.base_image is None:
            return
        self.query_one("#map", MapImage).image = render.highlight(
            self.base_image, field, self.bbox
        )
        score = "" if field["score"] is None else f"\nscore  [b]{field['score']}[/b]"
        self.query_one("#detail", Static).update(
            f"[b #C0D85B]{field['id']}[/]\n"
            f"area   [b]{field['area_ha']:,.2f}[/b] ha{score}"
        )

    # Detection

    def action_detect(self) -> None:
        if missing := tui_detect.missing():
            self.notify(
                f"Detecting fields needs the inference extra ({', '.join(missing)} "
                "missing): pip install 'ftw-cli[inference]'",
                severity="error",
                timeout=8,
            )
            return
        out = (
            Path.cwd() / f"ftw-detect-{self.year}-{self.bbox_text().replace(',', '_')}"
        )
        screen = tui_detect.DetectScreen(
            self.bbox, self.year, out, models.DEFAULT_MODEL
        )
        self.push_screen(screen, self._detected)

    def _detected(self, fields: list[dict] | None) -> None:
        if fields is None:
            return
        self.detected = fields
        self.notify(f"The model found {len(fields):,} fields. m switches maps.")
        self.source = "edition"
        self.action_toggle_source()

    def action_toggle_source(self) -> None:
        if self.detected is None:
            self.notify("Press d to detect fields here first.")
            return
        self.source = "model" if self.source == "edition" else "edition"
        self.run_render()

    @work(thread=True, exclusive=True, group="render")
    def run_render(self) -> None:
        if self.source == "model":
            fields = self.detected or []
            image = render.render(fields, self.bbox, fill=MODEL_FILL)
        else:
            fields = self.edition
            image = render.render(fields, self.bbox)
        self.call_from_thread(
            self._show_fields, fields, image, f"{len(fields):,} fields"
        )

    # Export

    def action_export(self, suffix: str = ".parquet") -> None:
        self.notify(f"Exporting {suffix}…")
        self.run_export(self.bbox, self.year, suffix)

    @work(thread=True, group="export")
    def run_export(self, bbox: ftw2e.BBox, year: int, suffix: str) -> None:
        corners = "_".join(f"{v:g}" for v in vars(bbox).values())
        out = Path(f"ftw-{year}-{corners}{suffix}")
        try:
            ftw2e.export(ftw2e.connect(), bbox, year, out, limit=None)
        except (duckdb.Error, OSError, LookupError, ValueError) as e:
            self.call_from_thread(self.notify, f"Export failed: {e}", severity="error")
            return
        self.call_from_thread(self.notify, f"Wrote {out.resolve()}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        actions = {
            "query": self.action_query,
            "detect": self.action_detect,
            "export": self.action_export,
        }
        if action := actions.get(event.button.id or ""):
            action()


def main() -> None:
    FtwApp().run()
