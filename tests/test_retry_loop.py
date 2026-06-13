"""L4 retry guidance: attempt counting, repeat detection, escalation,
and the patch_tool integration that feeds the host model's next try."""

from __future__ import annotations

from pathlib import Path

import pytest

from cluxion_agentplugin_supercoder import runner
from cluxion_agentplugin_supercoder.core import retry_loop
from cluxion_agentplugin_supercoder.core.hash_patch import file_hash


@pytest.fixture(autouse=True)
def _clean_state():
    retry_loop.reset()
    yield
    retry_loop.reset()


def test_attempts_count_up_and_escalate() -> None:
    first = retry_loop.record_failure("/ws", "a.py", "no_match", old_text="x")
    second = retry_loop.record_failure("/ws", "a.py", "no_match", old_text="y")
    third = retry_loop.record_failure("/ws", "a.py", "stale_file", old_text="z")
    assert (first.attempt, second.attempt, third.attempt) == (1, 2, 3)
    assert not first.escalate and not second.escalate
    assert third.escalate
    assert "Stop patching" in third.guidance


def test_identical_retry_is_called_out() -> None:
    retry_loop.record_failure("/ws", "a.py", "no_match", old_text="same")
    repeat = retry_loop.record_failure("/ws", "a.py", "no_match", old_text="same")
    assert repeat.repeated_input is True
    assert "Do not resend" in repeat.guidance
    changed = retry_loop.record_failure("/ws", "b.py", "no_match", old_text="same")
    assert changed.repeated_input is False


def test_success_resets_history() -> None:
    retry_loop.record_failure("/ws", "a.py", "no_match", old_text="x")
    retry_loop.record_success("/ws", "a.py")
    fresh = retry_loop.record_failure("/ws", "a.py", "no_match", old_text="x")
    assert fresh.attempt == 1


def test_tracking_is_bounded() -> None:
    for index in range(retry_loop.MAX_TRACKED_FILES + 10):
        retry_loop.record_failure("/ws", f"file_{index}.py", "no_match", old_text="x")
    assert len(retry_loop._failures) == retry_loop.MAX_TRACKED_FILES


def test_patch_failure_carries_retry_advice(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    payload = {
        "cwd": str(tmp_path),
        "path": "mod.py",
        "old_text": "not in the file",
        "new_text": "whatever",
    }
    first = runner.patch_tool(payload)
    assert first.ok is False
    assert first.payload["retry"]["attempt"] == 1
    assert "read_window" in first.payload["retry"]["guidance"]
    repeat = runner.patch_tool(payload)
    assert repeat.payload["retry"]["repeated_input"] is True
    third = runner.patch_tool(payload)
    assert third.payload["retry"]["escalate"] is True


def test_syntax_revert_counts_as_attempt_and_success_clears(tmp_path: Path) -> None:
    original = "def add(a, b):\n    return a + b\n"
    (tmp_path / "mod.py").write_text(original, encoding="utf-8")
    broken = runner.patch_tool(
        {
            "cwd": str(tmp_path),
            "path": "mod.py",
            "old_text": "return a + b",
            "new_text": "return a +",
        }
    )
    assert broken.payload["strategy"] == "syntax_reverted"
    assert broken.payload["retry"]["attempt"] == 1
    assert "syntax_errors" in broken.payload["retry"]["guidance"]
    fixed = runner.patch_tool(
        {
            "cwd": str(tmp_path),
            "path": "mod.py",
            "old_text": "return a + b",
            "new_text": "return a * b",
            "expected_file_hash": file_hash(original),
        }
    )
    assert fixed.ok is True
    assert "retry" not in fixed.payload
    fresh = retry_loop.record_failure(str(tmp_path), "mod.py", "no_match", old_text="q")
    assert fresh.attempt == 1
