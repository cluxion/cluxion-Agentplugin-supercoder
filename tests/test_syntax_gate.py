"""L1 syntax gate: cross-backend agreement on valid/invalid verdicts plus
the patch auto-revert loop. Error messages differ between tree-sitter and
the stdlib parsers, so assertions are on verdicts and locations, not text."""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

from cluxion_agentplugin_supercoder import runner, rust_bridge
from cluxion_agentplugin_supercoder.core import syntax_gate
from cluxion_agentplugin_supercoder.core.hash_patch import file_hash

_LOCAL_BIN = (
    Path(__file__).resolve().parents[1] / "rust" / "supercoder_index" / "target" / "release" / "supercoder-index"
)

BACKENDS = ["python"]
if importlib.util.find_spec("supercoder_index_native") is not None:
    BACKENDS.append("native")
if _LOCAL_BIN.exists() or shutil.which("supercoder-index"):
    BACKENDS.append("subprocess")


@pytest.fixture(params=BACKENDS)
def backend(request, monkeypatch):
    monkeypatch.setenv(rust_bridge.INDEX_BACKEND_ENV, request.param)
    if request.param == "subprocess" and _LOCAL_BIN.exists():
        monkeypatch.setenv(rust_bridge.INDEX_BIN_ENV, str(_LOCAL_BIN))
    return request.param


_RESULT_KEYS = {"ok", "checked", "language", "valid", "errors", "error_count"}


def test_valid_python_passes(backend: str) -> None:
    result = syntax_gate.check_source(content="def add(a, b):\n    return a + b\n", language="python")
    assert set(result) >= _RESULT_KEYS
    assert result["checked"] is True
    assert result["valid"] is True
    assert result["errors"] == []


def test_broken_python_reports_location(backend: str) -> None:
    result = syntax_gate.check_source(content="def add(a, b:\n    return a + b\n", language="python")
    assert result["valid"] is False
    assert result["error_count"] >= 1
    first = result["errors"][0]
    assert first["line"] >= 1
    assert first["kind"] in {"error", "missing"}


def test_json_verdicts(backend: str) -> None:
    assert syntax_gate.check_source(content='{"a": 1}', language="json")["valid"] is True
    assert syntax_gate.check_source(content='{"a": 1,}', language="json")["valid"] is False


def test_toml_routes_to_python_everywhere(backend: str) -> None:
    assert syntax_gate.check_source(content="a = 1\n", language="toml")["valid"] is True
    broken = syntax_gate.check_source(content="a = = 1\n", language="toml")
    assert broken["checked"] is True
    assert broken["valid"] is False


def test_unknown_language_is_fail_open(backend: str) -> None:
    result = syntax_gate.check_source(content="anything at all", language="")
    assert result["checked"] is False
    assert result["valid"] is True
    assert result["reason"] == "no_parser"


def test_rust_checked_only_with_treesitter(backend: str) -> None:
    result = syntax_gate.check_source(content="fn main( {", language="rust")
    if backend == "python":
        assert result["checked"] is False
    else:
        assert result["checked"] is True
        assert result["valid"] is False


def test_language_detection() -> None:
    assert syntax_gate.language_for_path("src/app.py") == "python"
    assert syntax_gate.language_for_path("ui/View.tsx") == "tsx"
    assert syntax_gate.language_for_path("Cargo.toml") == "toml"
    assert syntax_gate.language_for_path("notes.txt") is None


def _write(path: Path, text: str) -> str:
    path.write_text(text, encoding="utf-8")
    return file_hash(text)


def test_patch_reverts_on_broken_syntax(tmp_path: Path, backend: str) -> None:
    original = "def add(a, b):\n    return a + b\n"
    digest = _write(tmp_path / "mod.py", original)
    result = runner.patch_tool(
        {
            "cwd": str(tmp_path),
            "path": "mod.py",
            "old_text": "    return a + b\n",
            "new_text": "    return a +\n",
            "expected_file_hash": digest,
        }
    )
    assert result.ok is False
    assert result.payload["strategy"] == "syntax_reverted"
    assert result.payload["syntax_errors"]
    assert (tmp_path / "mod.py").read_text(encoding="utf-8") == original


def test_patch_passes_gate_when_valid(tmp_path: Path, backend: str) -> None:
    digest = _write(tmp_path / "mod.py", "def add(a, b):\n    return a + b\n")
    result = runner.patch_tool(
        {
            "cwd": str(tmp_path),
            "path": "mod.py",
            "old_text": "return a + b",
            "new_text": "return a * b",
            "expected_file_hash": digest,
        }
    )
    assert result.ok is True
    assert result.payload["syntax"] == {"checked": True, "language": "python", "valid": True, "error_count": 0}


def test_patch_gate_can_be_disabled(tmp_path: Path, backend: str) -> None:
    digest = _write(tmp_path / "mod.py", "def add(a, b):\n    return a + b\n")
    result = runner.patch_tool(
        {
            "cwd": str(tmp_path),
            "path": "mod.py",
            "old_text": "return a + b",
            "new_text": "return a +",
            "expected_file_hash": digest,
            "syntax_gate": False,
        }
    )
    assert result.ok is True
    assert "syntax" not in result.payload


def test_syntax_gate_tool_with_file(tmp_path: Path, backend: str) -> None:
    _write(tmp_path / "broken.json", '{"a": 1,}')
    result = runner.syntax_gate_tool({"cwd": str(tmp_path), "path": "broken.json"})
    assert result.ok is True
    assert result.payload["valid"] is False
    assert result.payload["language"] == "json"


def test_syntax_gate_tool_accepts_files_changed(tmp_path: Path, backend: str) -> None:
    _write(tmp_path / "ok.py", "def f():\n    return 1\n")
    _write(tmp_path / "broken.json", '{"a": 1,}')
    result = runner.syntax_gate_tool({"cwd": str(tmp_path), "files_changed": ["ok.py", "broken.json"]})
    assert result.ok is True
    assert [item["path"] for item in result.payload["files"]] == ["ok.py", "broken.json"]
    assert [item["valid"] for item in result.payload["files"]] == [True, False]
