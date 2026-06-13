from __future__ import annotations

from pathlib import Path

from cluxion_agentplugin_supercoder.core.safety import pre_tool_gate


def test_allows_plain_call(tmp_path: Path) -> None:
    decision = pre_tool_gate("patch", {"path": "src/app.py"}, workspace=tmp_path)
    assert decision.decision == "allow"


def test_stale_cursor_blocks_first(tmp_path: Path) -> None:
    decision = pre_tool_gate("patch", {"path": "src/app.py"}, workspace=tmp_path, stale_cursor=True)
    assert decision.decision == "block"
    assert "stale cursor" in decision.reason


def test_destructive_command_blocked_case_insensitive(tmp_path: Path) -> None:
    for command in ("rm -rf /", "Git Reset --HARD", "DROP TABLE users;"):
        decision = pre_tool_gate("terminal", {"command": command}, workspace=tmp_path)
        assert decision.decision == "block", command


def test_relative_workspace_escape_blocked(tmp_path: Path) -> None:
    decision = pre_tool_gate("patch", {"path": "../outside.txt"}, workspace=tmp_path)
    assert decision.decision == "block"
    assert "escape" in decision.reason


def test_sibling_directory_prefix_is_not_containment(tmp_path: Path) -> None:
    # Regression: /work2/file shares the string prefix /work but is outside.
    workspace = tmp_path / "work"
    workspace.mkdir()
    sibling = tmp_path / "work2" / "file.txt"
    decision = pre_tool_gate("patch", {"path": str(sibling)}, workspace=workspace)
    assert decision.decision == "block"
    assert "escape" in decision.reason


def test_secret_paths_blocked(tmp_path: Path) -> None:
    for rel in (".env", "config/credentials/db.json", "keys/id_rsa"):
        decision = pre_tool_gate("patch", {"path": rel}, workspace=tmp_path)
        assert decision.decision == "block", rel
        assert "secret" in decision.reason


def test_oversized_write_blocked_only_for_write_tools(tmp_path: Path) -> None:
    blocked = pre_tool_gate("write_file", {"path": "a.py", "line_count": 401}, workspace=tmp_path)
    assert blocked.decision == "block"
    allowed = pre_tool_gate("read_window", {"path": "a.py", "line_count": 401}, workspace=tmp_path)
    assert allowed.decision == "allow"
