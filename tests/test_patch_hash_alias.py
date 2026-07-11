from __future__ import annotations

from pathlib import Path

from cluxion_agentplugin_supercoder.core.hash_patch import file_hash
from cluxion_agentplugin_supercoder.runner import patch_tool
from cluxion_agentplugin_supercoder.schemas import PATCH_SCHEMA


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
    assert (tmp_path / "m.py").read_text(encoding="utf-8") == "x = 1\n"


def test_correct_hash_via_alias_applies(tmp_path: Path) -> None:
    target = tmp_path / "m.py"
    target.write_text("x = 1\n", encoding="utf-8")
    out = _patch(tmp_path, {"expected_hash": file_hash("x = 1\n")})
    assert out["ok"] is True
    assert target.read_text(encoding="utf-8") == "x = 2\n"


def test_correct_expected_file_hash_applies(tmp_path: Path) -> None:
    target = tmp_path / "m.py"
    target.write_text("x = 1\n", encoding="utf-8")
    out = _patch(tmp_path, {"expected_file_hash": file_hash("x = 1\n")})
    assert out["ok"] is True
    assert target.read_text(encoding="utf-8") == "x = 2\n"


def test_missing_both_hash_aliases_is_structured_failure_without_mutation(tmp_path: Path) -> None:
    target = tmp_path / "m.py"
    target.write_text("x = 1\n", encoding="utf-8")
    out = _patch(tmp_path, {})
    assert out["ok"] is False
    assert out.get("error") == "invalid_request"
    assert "expected" in str(out.get("message", "")).lower() or "hash" in str(out.get("message", "")).lower()
    assert out.get("hint")
    assert target.read_bytes() == b"x = 1\n"


def test_empty_string_hash_aliases_is_structured_failure_without_mutation(tmp_path: Path) -> None:
    target = tmp_path / "m.py"
    target.write_text("x = 1\n", encoding="utf-8")
    out = _patch(tmp_path, {"expected_file_hash": "", "expected_hash": ""})
    assert out["ok"] is False
    assert out.get("error") == "invalid_request"
    assert "expected" in str(out.get("message", "")).lower() or "hash" in str(out.get("message", "")).lower()
    assert out.get("hint")
    assert target.read_bytes() == b"x = 1\n"


def test_patch_schema_requires_at_least_one_hash_alias_via_anyof() -> None:
    params = PATCH_SCHEMA["parameters"]
    any_of = params.get("anyOf")
    assert isinstance(any_of, list) and any_of, "PATCH_SCHEMA.parameters must use anyOf for hash aliases"
    required_sets = {frozenset(clause.get("required", [])) for clause in any_of}
    assert frozenset({"expected_file_hash"}) in required_sets
    assert frozenset({"expected_hash"}) in required_sets
    # Do not require both aliases together; only at-least-one via anyOf.
    assert frozenset({"expected_file_hash", "expected_hash"}) not in required_sets
    # Keep the base required fields unchanged; do not fold hash keys into them.
    assert set(params.get("required", [])) == {"path", "old_text", "new_text"}
