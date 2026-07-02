from __future__ import annotations

import json
import tomllib
from pathlib import Path

import yaml


def test_root_plugin_artifacts_are_version_synced() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    version = pyproject["project"]["version"]

    claude = json.loads(Path(".claude-plugin/plugin.json").read_text(encoding="utf-8"))
    codex = json.loads(Path(".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    root_yaml = yaml.safe_load(Path("plugin.yaml").read_text(encoding="utf-8"))

    assert claude["version"] == version
    assert codex["version"] == version
    assert str(root_yaml["version"]) == version
    assert Path("commands/supercoder.md").is_file()
    assert Path("commands/supercoder-doctor.md").is_file()
    assert Path("skills/supercoder/SKILL.md").is_file()


def test_per_surface_adapter_forks_are_removed() -> None:
    assert not Path("adapters/codex").exists()
    assert not Path("adapters/claude").exists()
def test_marketplace_manifest_is_version_synced() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    version = pyproject["project"]["version"]

    marketplace = json.loads(Path(".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
    assert marketplace["plugins"][0]["version"] == version
    assert marketplace["plugins"][0]["source"] == "./"
