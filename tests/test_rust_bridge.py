"""Parity tests: the three index backends must produce identical scans."""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

from cluxion_agentplugin_supercoder import rust_bridge
from cluxion_agentplugin_supercoder.core.cursor import cursor_map
from cluxion_agentplugin_supercoder.core.hash_patch import file_hash

_LOCAL_BIN = (
    Path(__file__).resolve().parents[1] / "rust" / "supercoder_index" / "target" / "release" / "supercoder-index"
)

BACKENDS = ["python"]
if importlib.util.find_spec("supercoder_index_native") is not None:
    BACKENDS.append("native")
if _LOCAL_BIN.exists() or shutil.which("supercoder-index"):
    BACKENDS.append("subprocess")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (tmp_path / "src" / "lib.rs").write_text("fn main() {}\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# readme\r\nwindows line\r\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("ignored extension\n", encoding="utf-8")
    skipped = tmp_path / "node_modules" / "pkg"
    skipped.mkdir(parents=True)
    (skipped / "index.js").write_text("skip me\n", encoding="utf-8")
    return tmp_path


@pytest.fixture(params=BACKENDS)
def backend(request, monkeypatch):
    monkeypatch.setenv(rust_bridge.INDEX_BACKEND_ENV, request.param)
    if request.param == "subprocess" and _LOCAL_BIN.exists():
        monkeypatch.setenv(rust_bridge.INDEX_BIN_ENV, str(_LOCAL_BIN))
    return request.param


def test_scan_entries(repo: Path, backend: str) -> None:
    entries = rust_bridge.scan_repo(repo)
    paths = [entry["path"] for entry in entries]
    assert paths == ["README.md", "src/lib.rs", "src/main.py"]
    by_path = {entry["path"]: entry for entry in entries}
    assert by_path["src/main.py"]["total_lines"] == 2
    assert by_path["src/main.py"]["file_hash"] == file_hash("print('hi')\n")


def test_scan_hash_normalizes_crlf(repo: Path, backend: str) -> None:
    entries = {entry["path"]: entry for entry in rust_bridge.scan_repo(repo)}
    assert entries["README.md"]["file_hash"] == file_hash("# readme\nwindows line\n")


def test_scan_respects_max_files(repo: Path, backend: str) -> None:
    entries = rust_bridge.scan_repo(repo, max_files=2)
    assert [entry["path"] for entry in entries] == ["README.md", "src/lib.rs"]


def test_cursor_map_uses_scan(repo: Path, backend: str) -> None:
    entries = cursor_map(repo)
    assert all(entry["purpose"] == "index" for entry in entries)
    assert [entry["path"] for entry in entries] == ["README.md", "src/lib.rs", "src/main.py"]


def test_resolve_backend_honors_env(backend: str) -> None:
    assert rust_bridge.resolve_backend() == backend


def test_scan_root_under_skip_named_ancestor(tmp_path: Path, backend: str) -> None:
    # regression: a repo whose own path sits under a skip-named dir (…/target/repo) must
    # still index — SKIP_DIRS applies within-tree, not to root's ancestry (rust filter_entry parity)
    root = tmp_path / "target" / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (root / "b.py").write_text("y = 2\n", encoding="utf-8")
    assert [e["path"] for e in rust_bridge.scan_repo(root)] == ["b.py", "src/a.py"]


def test_backend_parity(repo: Path) -> None:
    if len(BACKENDS) < 2:
        pytest.skip("only one backend available")
    import os

    results = {}
    for name in BACKENDS:
        os.environ[rust_bridge.INDEX_BACKEND_ENV] = name
        if name == "subprocess" and _LOCAL_BIN.exists():
            os.environ[rust_bridge.INDEX_BIN_ENV] = str(_LOCAL_BIN)
        try:
            results[name] = rust_bridge.scan_repo(repo)
        finally:
            os.environ.pop(rust_bridge.INDEX_BACKEND_ENV, None)
            os.environ.pop(rust_bridge.INDEX_BIN_ENV, None)
    baseline = results["python"]
    for name, entries in results.items():
        assert entries == baseline, f"{name} diverges from python baseline"


def test_subprocess_json_bridge_uses_utf8_encoding(monkeypatch) -> None:
    """C/ASCII locale must not crash after a non-ASCII payload is already committed."""
    captured: dict[str, object] = {}

    class Completed:
        returncode = 0
        stdout = '{"ok": true, "matched": false}'
        stderr = ""

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        return Completed()

    monkeypatch.setattr(rust_bridge, "resolve_backend", lambda: "subprocess")
    monkeypatch.setattr(rust_bridge.shutil, "which", lambda _name: "/usr/bin/supercoder-index")
    monkeypatch.setattr(rust_bridge.subprocess, "run", fake_run)
    result = rust_bridge._invoke_subprocess("fuzzy_span", {"text": "한글\n", "reference": "한글\n"})
    assert result["ok"] is True
    assert captured.get("encoding") == "utf-8"
    assert captured.get("text") is True


def test_skip_named_root_matches_python_scan(tmp_path: Path, backend: str) -> None:
    """Root basename in SKIP_DIRS (target/dist/.venv) must still be scanned."""
    for name in ("target", "dist", ".venv"):
        root = tmp_path / name
        (root / "src").mkdir(parents=True)
        (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
        (root / "b.py").write_text("y = 2\n", encoding="utf-8")
        paths = [entry["path"] for entry in rust_bridge.scan_repo(root)]
        assert paths == ["b.py", "src/a.py"], f"backend={backend} root={name}"
