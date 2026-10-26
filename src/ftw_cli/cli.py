"""The ``ftw`` command line."""

import sys
from pathlib import Path
from typing import Annotated

import duckdb
from cyclopts import App, Parameter
from rich.panel import Panel
from rich.table import Table

from ftw_cli import __version__, output
from ftw_cli.cli_data import data, view
from ftw_cli.cli_detect import detect, imagery, models, polygonize, predict
from ftw_cli.cli_fields import compare, stats
from ftw_cli.progress import spinner

app = App(
    name="ftw",
    version=__version__,
    help="Fields of The World: agricultural field boundaries.",
    default_parameter=Parameter(negative=""),
)
commands = (data, view, detect, predict, polygonize, compare, stats, imagery, models)
for command in commands:
    app.command(command)
app.register_install_completion_command()

EXAMPLES = [
    ("Explore", 'ftw data query "Story County, Iowa"'),
    ("", 'ftw data query "Beauce, France" --year all'),
    ("", 'ftw view "Chartres, France" --out chartres.png'),
    ("Detect", 'ftw detect "Poltava, Ukraine" --year 2024 --out poltava.gpkg'),
]
# Errors that are the input's or the network's fault: one line, no traceback.
EXPECTED = (LookupError, ValueError, OSError, duckdb.Error)


@app.default
def home():
    """A few commands to start from."""
    if not output.console.is_terminal:
        app.help_print()
        return
    examples = Table.grid(padding=(0, 2))
    for group, command in EXAMPLES:
        examples.add_row(f"[bold #C0D85B]{group}[/]", command)
    output.console.print(
        Panel(
            examples,
            title=f"[bold #C0D85B]ftw[/] Fields of The World [dim]{__version__}[/]",
            subtitle="[dim]ftw --help · ftw --install-completion[/]",
            subtitle_align="left",
            border_style="#22A070",
            padding=(1, 2),
            expand=False,
        )
    )


@app.meta.default
def _meta(
    *tokens: Annotated[str, Parameter(show=False, allow_leading_hyphen=True)],
    json: Annotated[
        bool | None,
        Parameter(
            negative="", help="Print JSON. Default when stdout is not a terminal."
        ),
    ] = None,
):
    output.configure(json)
    return app(tokens)


@app.command
def doctor():
    """Check the install and network access."""
    from ftw_cli import doctor as checks

    with spinner("Checking the install and the network"):
        rows = checks.run()
    output.emit(rows, title="ftw doctor")
    if any(r["status"] == "failed" for r in rows):
        raise SystemExit(1)


@app.command
def schema(
    *command: Annotated[str, Parameter(help="A command, such as `data query`.")],
):
    """Print command parameters as JSON Schema."""
    from ftw_cli import schema as schemas

    found = schemas.commands(app)
    if command:
        name = " ".join(("ftw", *command))
        if name not in found:
            raise LookupError(f"no command {name!r}; try one of: {', '.join(found)}")
        found = {name: found[name]}
    output.print_json({name: schemas.schema(sub) for name, sub in found.items()})


@app.command
def skill(
    *,
    out: Annotated[
        Path | None,
        Parameter(help="Install as DIR/ftw/SKILL.md, e.g. ~/.claude/skills."),
    ] = None,
):
    """Print the agent skill, or install it with --out."""
    packaged = Path(__file__).parent / "SKILL.md"
    # Editable installs don't copy it into the package; read it from the repo instead.
    source = Path(__file__).parents[2] / "skills" / "ftw" / "SKILL.md"
    text = (packaged if packaged.exists() else source).read_text()
    if out is None:
        sys.stdout.write(text)
        return
    path = out.expanduser() / "ftw" / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    output.emit({"path": str(path)})


def _report(kind, error, traceback):
    if issubclass(kind, EXPECTED):
        output.stderr.print(f"[red]error:[/red] {error}")
    else:
        sys.__excepthook__(kind, error, traceback)


def run():
    sys.excepthook = _report
    app.meta()
