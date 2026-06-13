"""Test gate (L3): map changed files to concrete test commands.
Layout-agnostic — test files are discovered repo-wide via the Rust-backed
index scan (three-tier fallback), covering src/, flat, and tests-beside-code
layouts. Non-Python changes route to cargo/npm/go when the project marker
exists. Suggest-only: the host terminal runs the command."""

from __future__ import annotations

import json
from pathlib import Path

from cluxion_agentplugin_supercoder import rust_bridge

_TEST_SCAN_MAX_FILES = 4096
_NODE_SUFFIXES = {".js", ".jsx", ".ts", ".tsx"}
_PYTHON_PROJECT_MARKERS = ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini")
_TEST_HINTS = (
    "def test_",
    "async def test_",
    "class Test",
    "import pytest",
    "from pytest",
    "import unittest",
    "from unittest",
)


def _is_test_file(path: Path) -> bool:
    return path.suffix == ".py" and (path.stem.startswith("test_") or path.stem.endswith("_test"))


def _looks_like_test(full: Path) -> bool:
    # Name alone is not proof (a source module can be called test_gate.py);
    # hints are line-start anchored so words inside string literals do not count.
    try:
        text = full.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return any(line.lstrip().startswith(_TEST_HINTS) for line in text.splitlines())


def _test_inventory(root: Path) -> list[Path]:
    entries = rust_bridge.scan_repo(root, max_files=_TEST_SCAN_MAX_FILES, extensions=(".py",))
    inventory: list[Path] = []
    for entry in entries:
        rel = Path(str(entry.get("path", "")))
        if _is_test_file(rel):
            inventory.append(rel)
    return inventory


def _proximity(changed: Path, candidate: Path) -> tuple[int, int, str]:
    shared = 0
    for ours, theirs in zip(changed.parts, candidate.parts, strict=False):
        if ours != theirs:
            break
        shared += 1
    return (-shared, len(candidate.parts), str(candidate))


def _match_tests(changed: Path, pool: list[Path]) -> list[str]:
    stems = [f"test_{changed.stem}", f"{changed.stem}_test"]
    parent = changed.parent.name
    if parent and parent != "src":
        stems += [f"test_{parent}", f"{parent}_test"]
    for stem in stems:
        matches = [candidate for candidate in pool if candidate.stem == stem]
        if matches:
            matches.sort(key=lambda candidate: _proximity(changed, candidate))
            return [str(matches[0])]
    return _fuzzy_match(changed, pool)


def _fuzzy_match(changed: Path, pool: list[Path]) -> list[str]:
    # Expanding: test files that extend the module name (pruner -> test_pruner_archive).
    prefix = f"test_{changed.stem}_"
    expanded = [candidate for candidate in pool if candidate.stem.startswith(prefix)]
    if expanded:
        expanded.sort(key=lambda candidate: _proximity(changed, candidate))
        return [str(candidate) for candidate in expanded[:3]]
    # Shrinking: drop trailing name tokens (guard_bridge -> test_guard).
    tokens = changed.stem.split("_")
    for cut in range(len(tokens) - 1, 0, -1):
        head = "_".join(tokens[:cut])
        matches = [candidate for candidate in pool if candidate.stem in (f"test_{head}", f"{head}_test")]
        if matches:
            matches.sort(key=lambda candidate: _proximity(changed, candidate))
            return [str(matches[0])]
    return []


def _resolve_test_targets(root: Path, files_changed: list[str]) -> list[str]:
    pool: list[Path] | None = None
    targets: list[str] = []
    for raw in files_changed:
        rel = raw.strip()
        if not rel:
            continue
        path = Path(rel)
        if _is_test_file(path):
            full = root / path
            if not full.exists():
                continue
            if _looks_like_test(full):
                targets.append(str(path))
                continue
            # Named like a test but a source module: fall through to mapping.
        elif path.name == "conftest.py" and path.parent.name:
            # Fixtures changed: rerun the directory the conftest governs.
            if (root / path).exists():
                targets.append(str(path.parent))
            continue
        if path.suffix != ".py":
            continue
        if pool is None:
            pool = [rel for rel in _test_inventory(root) if _looks_like_test(root / rel)]
        targets.extend(_match_tests(path, pool))
    return list(dict.fromkeys(targets))


def _npm_test_script(root: Path) -> bool:
    manifest = root / "package.json"
    if not manifest.exists():
        return False
    try:
        scripts = json.loads(manifest.read_text(encoding="utf-8")).get("scripts", {})
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(scripts, dict) and bool(str(scripts.get("test", "")).strip())


def _project_runners(root: Path, suffixes: set[str]) -> list[dict[str, str]]:
    runners: list[dict[str, str]] = []
    if ".rs" in suffixes and (root / "Cargo.toml").exists():
        runners.append({"language": "rust", "command": "cargo test -q"})
    if ".go" in suffixes and (root / "go.mod").exists():
        runners.append({"language": "go", "command": "go test ./..."})
    if suffixes & _NODE_SUFFIXES and _npm_test_script(root):
        runners.append({"language": "node", "command": "npm test --silent"})
    return runners


def _default_command(root: Path) -> tuple[str, str]:
    if any((root / marker).exists() for marker in _PYTHON_PROJECT_MARKERS) or (root / "tests").is_dir():
        return "pytest -q", "project_default"
    if (root / "Cargo.toml").exists():
        return "cargo test -q", "project_default"
    if _npm_test_script(root):
        return "npm test --silent", "project_default"
    if (root / "go.mod").exists():
        return "go test ./...", "project_default"
    return "pytest -q", "default"


def suggest_test_commands(
    files_changed: list[str] | None = None,
    *,
    command: str | None = None,
    cwd: Path | None = None,
) -> dict[str, object]:
    root = (cwd or Path.cwd()).expanduser().resolve()
    changed = [str(item) for item in (files_changed or []) if str(item).strip()]
    targets = _resolve_test_targets(root, changed) if changed else []
    suffixes = {Path(item).suffix for item in changed}
    runners = _project_runners(root, suffixes)
    explicit = (command or "").strip()
    if explicit and explicit != "pytest -q":
        primary = explicit
        source = "explicit_command"
    elif targets:
        primary = "pytest -q " + " ".join(targets)
        source = "mapped_from_files_changed"
    elif runners and ".py" not in suffixes:
        primary = runners[0]["command"]
        source = "project_runner"
    elif explicit:
        primary = explicit
        source = "default"
    else:
        primary, source = _default_command(root)
    return {
        "ok": True,
        "mode": "suggest_or_run",
        "command": primary,
        "targets": targets,
        "files_changed": changed,
        "source": source,
        "runners": runners,
        "alternatives": _alternatives(targets, runners, primary),
        "note": (
            "Run through host terminal tool; do not claim pass unless command succeeded. "
            "Record stdout/stderr in supercoder_brief.tests_run."
        ),
    }


def _alternatives(targets: list[str], runners: list[dict[str, str]], primary: str) -> list[str]:
    if targets:
        joined = " ".join(targets)
        options = [f"pytest -q {joined}", f"python -m pytest -q {joined}"]
    else:
        options = ["pytest -q", "python -m pytest -q"]
    options.extend(runner["command"] for runner in runners)
    return [option for option in dict.fromkeys(options) if option != primary]


__all__ = ["suggest_test_commands"]
