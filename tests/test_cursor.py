from __future__ import annotations

from pathlib import Path

import pytest

from cluxion_agentplugin_supercoder import runner
from cluxion_agentplugin_supercoder.core.cursor import read_window


def test_read_window_bounds(tmp_path: Path) -> None:
    path = tmp_path / "sample.py"
    path.write_text("\n".join(f"line{i}" for i in range(1, 201)), encoding="utf-8")
    window = read_window(tmp_path, "sample.py", start_line=10, max_lines=5)
    assert window.start_line == 10
    assert window.end_line == 14
    assert "line10" in window.content


@pytest.mark.parametrize("rel", [".env", "config/credentials/db.json"])
def test_read_window_blocks_secret_files(tmp_path: Path, rel: str) -> None:
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("AWS_SECRET=leaked", encoding="utf-8")
    with pytest.raises(PermissionError, match="secret file access blocked"):
        read_window(tmp_path, rel)


def test_read_window_blocks_sibling_directory_prefix_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "work"
    workspace.mkdir()
    sibling = tmp_path / "work2"
    sibling.mkdir()
    (sibling / "secret.py").write_text("leaked", encoding="utf-8")
    with pytest.raises(PermissionError, match="workspace escape blocked"):
        read_window(workspace, "../work2/secret.py")


def test_read_window_blocks_plain_traversal(tmp_path: Path) -> None:
    workspace = tmp_path / "work"
    workspace.mkdir()
    with pytest.raises(PermissionError, match="workspace escape blocked"):
        read_window(workspace, "../../etc/passwd")


@pytest.mark.parametrize("rel", [".env", "config/credentials/db.json"])
def test_read_window_tool_blocks_secret_files(tmp_path: Path, rel: str) -> None:
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("AWS_SECRET=leaked", encoding="utf-8")
    result = runner.read_window_tool({"cwd": str(tmp_path), "path": rel})
    assert result.ok is False
    assert result.payload["error"] == "secret file access blocked"


def test_read_window_tool_blocks_sibling_directory_prefix_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "work"
    workspace.mkdir()
    sibling = tmp_path / "work2"
    sibling.mkdir()
    (sibling / "secret.py").write_text("leaked", encoding="utf-8")
    result = runner.read_window_tool({"cwd": str(workspace), "path": "../work2/secret.py"})
    assert result.ok is False
    assert result.payload["error"] == "workspace escape blocked"


def test_read_window_tool_allows_normal_in_workspace_file(tmp_path: Path) -> None:
    path = tmp_path / "sample.py"
    path.write_text("print('ok')\n", encoding="utf-8")
    result = runner.read_window_tool({"cwd": str(tmp_path), "path": "sample.py"})
    assert result.ok is True
    assert "print('ok')" in str(result.payload["content"])


@pytest.mark.parametrize("field", ["start_line", "max_lines"])
def test_read_window_tool_rejects_non_positive_bounds(tmp_path: Path, field: str) -> None:
    path = tmp_path / "sample.py"
    path.write_text("print('ok')\n", encoding="utf-8")
    result = runner.read_window_tool({"cwd": str(tmp_path), "path": "sample.py", field: 0})
    assert result.ok is False
    assert result.payload == {"error": "invalid_request", "message": f"{field} must be >= 1", "hint": "Pass a positive integer."}
