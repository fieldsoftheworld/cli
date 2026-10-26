import json
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_plugin_marketplace_and_skill_names_match():
    # A mismatch makes `/plugin install ftw` fail with "not found in marketplace".
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    marketplace = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    [entry] = marketplace["plugins"]
    skill = (ROOT / "skills" / "ftw" / "SKILL.md").read_text()
    assert plugin["name"] == entry["name"] == "ftw"
    assert entry["source"] == "./"
    assert skill.startswith("---\nname: ftw\n")
