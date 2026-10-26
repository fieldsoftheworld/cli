import subprocess
import sys

from ftw_cli import schema
from ftw_cli.cli import app


def test_schema_describes_commands_as_typed():
    found = schema.commands(app)
    assert {"ftw detect", "ftw data query", "ftw imagery download"} <= set(found)

    detect = schema.schema(found["ftw detect"])
    assert detect["required"] == ["area", "year", "out"]
    assert detect["positional"] == ["area"]
    model = detect["properties"]["model"]
    assert model["default"] == "prue-ccby-b5" and "prue-ccby-b5" in model["enum"]
    assert detect["properties"]["keep"]["type"] == ["string", "null"]


def test_every_command_and_parameter_is_described():
    for name, command in schema.commands(app).items():
        described = schema.schema(command)
        assert described["description"], name
        for param, prop in described["properties"].items():
            assert prop["description"], f"{name} {param}"


def test_skill_installs_into_a_skills_directory(tmp_path):
    code = f"import sys; from ftw_cli.cli import run; sys.argv = ['ftw', 'skill', '--out', {str(tmp_path)!r}, '--json']; run()"
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True)
    text = (tmp_path / "ftw" / "SKILL.md").read_text()
    assert text.startswith("---\nname: ftw\ndescription: ")
