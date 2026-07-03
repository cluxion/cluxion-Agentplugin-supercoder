"""L2 lint gate: advisory ruff findings on the changed file. The gate is
suggest-only — assertions check that findings ride along without ever
flipping a successful patch to failure."""

from __future__ import annotations

from pathlib import Path

import pytest

from cluxion_agentplugin_supercoder import runner
from cluxion_agentplugin_supercoder.core import lint_gate
from cluxion_agentplugin_supercoder.core.hash_patch import file_hash

pytestmark = pytest.mark.skipif(lint_gate.ruff_bin() is None, reason="ruff not installed")


def test_clean_file_reports_clean(tmp_path: Path) -> None:
    target = tmp_path / "mod.py"
    target.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    result = lint_gate.check_file(target, cwd=tmp_path)
    assert result["checked"] is True
    assert result["tool"] == "ruff"
    assert result["clean"] is True
    assert result["findings"] == []


def test_unused_import_is_reported(tmp_path: Path) -> None:
    target = tmp_path / "mod.py"
    target.write_text("import os\n\n\ndef add(a, b):\n    return a + b\n", encoding="utf-8")
    result = lint_gate.check_file(target, cwd=tmp_path)
    assert result["clean"] is False
    first = result["findings"][0]
    assert first["code"] == "F401"
    assert first["line"] == 1
    assert isinstance(first["fixable"], bool)


def test_non_python_is_unchecked(tmp_path: Path) -> None:
    target = tmp_path / "lib.rs"
    target.write_text("fn main() {}\n", encoding="utf-8")
    result = lint_gate.check_file(target, cwd=tmp_path)
    assert result["checked"] is False
    assert result["reason"] == "no_linter"
    assert result["clean"] is True


def test_missing_tool_is_fail_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(lint_gate.RUFF_BIN_ENV, str(tmp_path / "nope"))
    target = tmp_path / "mod.py"
    target.write_text("import os\n", encoding="utf-8")
    result = lint_gate.check_file(target, cwd=tmp_path)
    assert result["checked"] is False
    assert result["reason"] == "no_tool"


def test_patch_carries_advisory_findings(tmp_path: Path) -> None:
    original = "def add(a, b):\n    return a + b\n"
    (tmp_path / "mod.py").write_text(original, encoding="utf-8")
    result = runner.patch_tool(
        {
            "cwd": str(tmp_path),
            "path": "mod.py",
            "old_text": "def add(a, b):\n",
            "new_text": "import os\n\n\ndef add(a, b):\n",
            "expected_file_hash": file_hash(original),
        }
    )
    assert result.ok is True
    lint = result.payload["lint"]
    assert lint["clean"] is False
    assert any(finding["code"] == "F401" for finding in lint["findings"])


def test_patch_lint_gate_can_be_disabled(tmp_path: Path) -> None:
    original = "def add(a, b):\n    return a + b\n"
    (tmp_path / "mod.py").write_text(original, encoding="utf-8")
    result = runner.patch_tool(
        {
            "cwd": str(tmp_path),
            "path": "mod.py",
            "old_text": "return a + b",
            "new_text": "return a * b",
            "expected_file_hash": file_hash(original),
            "lint_gate": False,
        }
    )
    assert result.ok is True
    assert "lint" not in result.payload


def test_lint_gate_tool(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text("import os\n", encoding="utf-8")
    result = runner.lint_gate_tool({"cwd": str(tmp_path), "path": "mod.py"})
    assert result.ok is True
    assert result.payload["clean"] is False
    assert result.payload["finding_count"] >= 1


def test_lint_gate_rejects_directory(tmp_path: Path) -> None:
    d = tmp_path / "emptydir"
    d.mkdir()
    result = lint_gate.check_file(d)
    assert result.get("ok") is False
    assert result.get("error") == "path is a directory"

    # also via runner tool
    res_tool = runner.lint_gate_tool({"cwd": str(tmp_path), "path": "emptydir"})
    assert res_tool.ok is False
    assert res_tool.payload.get("error") == "path is a directory"


def test_lint_gate_tool_accepts_files_changed(tmp_path: Path) -> None:
    (tmp_path / "clean.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tmp_path / "dirty.py").write_text("import os\n", encoding="utf-8")
    result = runner.lint_gate_tool({"cwd": str(tmp_path), "files_changed": ["clean.py", "dirty.py"]})
    assert result.ok is True
    assert [item["path"] for item in result.payload["files"]] == ["clean.py", "dirty.py"]
    assert [item["clean"] for item in result.payload["files"]] == [True, False]
