from __future__ import annotations

from pathlib import Path

from cluxion_agentplugin_supercoder.runner import patch_tool


def _patch(tmp_path: Path, extra: dict) -> dict:
    target = tmp_path / "m.py"
    if not target.exists():
        target.write_text("x = 1\n", encoding="utf-8")
    payload = {"cwd": str(tmp_path), "path": "m.py", "old_text": "x = 1\n", "new_text": "x = 2\n", **extra}
    result = patch_tool(payload)
    return {"ok": result.ok, **result.payload}


def test_wrong_expected_hash_via_alias_is_rejected(tmp_path: Path) -> None:
    out = _patch(tmp_path, {"expected_hash": "deadbeef" * 8})
    assert out["ok"] is False
    assert "changed" in out["message"] or "stale" in out["message"]
    assert (tmp_path / "m.py").read_text(encoding="utf-8") == "x = 1\n"


def test_wrong_expected_file_hash_still_rejected(tmp_path: Path) -> None:
    out = _patch(tmp_path, {"expected_file_hash": "deadbeef" * 8})
    assert out["ok"] is False


def test_correct_hash_via_alias_applies(tmp_path: Path) -> None:
    from cluxion_agentplugin_supercoder.core.hash_patch import file_hash

    target = tmp_path / "m.py"
    target.write_text("x = 1\n", encoding="utf-8")
    out = _patch(tmp_path, {"expected_hash": file_hash("x = 1\n")})
    assert out["ok"] is True
    assert target.read_text(encoding="utf-8") == "x = 2\n"
