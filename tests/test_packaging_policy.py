from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import yaml

# Discovery / public plugin identity (marketplace + surface manifests).
# Python dist name, CLI, wheels, and Hermes entry points stay on the legacy package id.
CANONICAL_PLUGIN_ID = "clx-supercoder"
PYTHON_DIST_NAME = "cluxion-agentplugin-supercoder"
PUBLIC_REPO_URL = "https://github.com/cluxion/clx-supercoder"


def test_root_plugin_artifacts_are_version_synced() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    version = pyproject["project"]["version"]

    claude = json.loads(Path(".claude-plugin/plugin.json").read_text(encoding="utf-8"))
    codex = json.loads(Path(".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    root_yaml = yaml.safe_load(Path("plugin.yaml").read_text(encoding="utf-8"))
    init_source = Path("src/cluxion_agentplugin_supercoder/__init__.py").read_text(encoding="utf-8")
    fallback = re.search(r'__version__ = "([^"]+)"', init_source)

    assert claude["name"] == CANONICAL_PLUGIN_ID
    assert codex["name"] == CANONICAL_PLUGIN_ID
    assert root_yaml["name"] == CANONICAL_PLUGIN_ID
    assert claude["version"] == version
    assert codex["version"] == version
    assert str(root_yaml["version"]) == version
    assert fallback is not None and fallback.group(1) == version
    assert Path("commands/supercoder.md").is_file()
    assert Path("commands/supercoder-doctor.md").is_file()
    skill_path = Path("skills/clx-supercoder/SKILL.md")
    assert skill_path.is_file()
    assert yaml.safe_load(skill_path.read_text(encoding="utf-8").split("---", 2)[1])["name"] == CANONICAL_PLUGIN_ID
    assert not Path("skills/supercoder").exists()


def test_per_surface_adapter_forks_are_removed() -> None:
    assert not Path("adapters/codex").exists()
    assert not Path("adapters/claude").exists()


def test_marketplace_manifest_is_version_synced() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    version = pyproject["project"]["version"]

    marketplace = json.loads(Path(".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
    assert marketplace["name"] == CANONICAL_PLUGIN_ID
    assert marketplace["plugins"][0]["name"] == CANONICAL_PLUGIN_ID
    assert marketplace["plugins"][0]["version"] == version
    assert marketplace["plugins"][0]["source"] == "./"


def test_python_dist_identity_stays_compat_while_public_urls_use_canonical() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]
    urls = project["urls"]
    scripts = project["scripts"]
    hermes = project["entry-points"]["hermes_agent.plugins"]

    assert project["name"] == PYTHON_DIST_NAME
    assert PYTHON_DIST_NAME in hermes
    assert "cluxion-supercoder" in scripts
    assert urls["Homepage"] == PUBLIC_REPO_URL
    assert urls["Repository"] == PUBLIC_REPO_URL
    assert urls["Issues"] == f"{PUBLIC_REPO_URL}/issues"
