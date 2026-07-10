"""L4 retry guidance: attempt counting, repeat detection, escalation,
persistence across one-shot CLI processes, and the patch_tool integration
that feeds the host model's next try."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
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


def test_workspaces_do_not_share_budget() -> None:
    for _ in range(retry_loop.MAX_ATTEMPTS):
        retry_loop.record_failure("/ws-a", "a.py", "no_match", old_text="x")
    other = retry_loop.record_failure("/ws-b", "a.py", "no_match", old_text="x")
    assert other.attempt == 1
    assert other.escalate is False


def test_tracking_is_bounded() -> None:
    for index in range(retry_loop.MAX_TRACKED_FILES + 10):
        retry_loop.record_failure("/ws", f"file_{index}.py", "no_match", old_text="x")
    tracked = list(retry_loop._state_dir().glob("*.json"))
    assert len(tracked) == retry_loop.MAX_TRACKED_FILES


def test_disk_state_expires_after_ttl() -> None:
    retry_loop.record_failure("/ws", "a.py", "no_match", old_text="x")
    state_file = next(retry_loop._state_dir().glob("*.json"))
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state["updated"] = time.time() - retry_loop.STATE_TTL_SECONDS - 1
    state_file.write_text(json.dumps(state), encoding="utf-8")
    fresh = retry_loop.record_failure("/ws", "a.py", "no_match", old_text="x")
    assert fresh.attempt == 1


def test_unwritable_state_dir_falls_back_to_memory(monkeypatch, tmp_path: Path) -> None:
    blocker = tmp_path / "blocked"
    blocker.write_text("", encoding="utf-8")
    monkeypatch.setenv("CLUXION_SUPERCODER_RETRY_DIR", str(blocker))
    attempts = [retry_loop.record_failure("/ws", "a.py", "no_match", old_text="x") for _ in range(3)]
    assert [advice.attempt for advice in attempts] == [1, 2, 3]
    assert attempts[-1].escalate is True
    assert retry_loop._failures


def test_escalation_survives_one_shot_cli_processes(tmp_path: Path) -> None:
    """The documented contract: each `patch` call is a fresh CLI process, and
    the third identical failure must still surface escalate=true."""
    (tmp_path / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    payload = json.dumps(
        {"cwd": str(tmp_path), "path": "mod.py", "old_text": "not in the file", "new_text": "whatever"}
    )
    env = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src") + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    advice = []
    for _ in range(retry_loop.MAX_ATTEMPTS):
        proc = subprocess.run(
            [sys.executable, "-m", "cluxion_agentplugin_supercoder.cli", "patch", "--json-stdin"],
            input=payload,
            capture_output=True,
            text=True,
            env=env,
        )
        assert proc.returncode == 1, proc.stderr
        advice.append(json.loads(proc.stdout)["retry"])
    assert [item["attempt"] for item in advice] == [1, 2, 3]
    assert [item["escalate"] for item in advice] == [False, False, True]
    assert advice[1]["repeated_input"] is True
    assert "Stop patching" in advice[2]["guidance"]


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


def test_retry_dir_symlink_falls_back_without_touching_victim(monkeypatch, tmp_path: Path) -> None:
    victim = tmp_path / "victim_dir"
    victim.mkdir(mode=0o700)
    marker = victim / "keep.txt"
    marker.write_text("safe\n", encoding="utf-8")
    link = tmp_path / "retry_link"
    link.symlink_to(victim)
    monkeypatch.setenv("CLUXION_SUPERCODER_RETRY_DIR", str(link))
    attempts = [retry_loop.record_failure("/ws", "a.py", "no_match", old_text="x") for _ in range(3)]
    assert [advice.attempt for advice in attempts] == [1, 2, 3]
    assert retry_loop._failures
    assert marker.read_text(encoding="utf-8") == "safe\n"
    assert list(victim.glob("*.json")) == []
    retry_loop.record_success("/ws", "a.py")
    retry_loop.reset()
    assert marker.read_text(encoding="utf-8") == "safe\n"
    assert list(victim.glob("*")) == [marker]


def test_retry_state_file_symlink_does_not_clobber_victim(monkeypatch, tmp_path: Path) -> None:
    state_dir = tmp_path / "retry_state"
    state_dir.mkdir(mode=0o700)
    monkeypatch.setenv("CLUXION_SUPERCODER_RETRY_DIR", str(state_dir))
    victim = tmp_path / "victim.json"
    victim.write_text("DO_NOT_CLOBBER\n", encoding="utf-8")
    state_path = retry_loop._state_path("/ws", "a.py")
    # Plant a symlink where the state file would live.
    if state_path.exists() or state_path.is_symlink():
        state_path.unlink()
    state_path.symlink_to(victim)
    advice = retry_loop.record_failure("/ws", "a.py", "no_match", old_text="x")
    # Unsafe file → memory fallback; victim must be untouched.
    assert advice.attempt == 1
    assert victim.read_text(encoding="utf-8") == "DO_NOT_CLOBBER\n"
    assert state_path.is_symlink()
    retry_loop.record_success("/ws", "a.py")
    assert victim.read_text(encoding="utf-8") == "DO_NOT_CLOBBER\n"
    assert state_path.is_symlink()


def test_retry_state_delete_opens_basename_nofollow_before_unlink(monkeypatch, tmp_path: Path) -> None:
    state_dir = tmp_path / "retry_state"
    state_dir.mkdir(mode=0o700)
    monkeypatch.setenv("CLUXION_SUPERCODER_RETRY_DIR", str(state_dir))
    retry_loop.record_failure("/ws", "a.py", "no_match", old_text="x")
    state_path = retry_loop._state_path("/ws", "a.py")
    assert state_path.exists()

    real_open = retry_loop.os.open
    opened_basename = False

    def spy_open(path, flags, *args, **kwargs):
        nonlocal opened_basename
        if path == state_path.name and kwargs.get("dir_fd") is not None:
            opened_basename = True
            assert flags & retry_loop.os.O_NOFOLLOW
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(retry_loop.os, "open", spy_open)
    retry_loop.record_success("/ws", "a.py")

    assert opened_basename is True
    assert not state_path.exists()


def test_retry_dir_wrong_mode_falls_back_without_chmod(monkeypatch, tmp_path: Path) -> None:
    state_dir = tmp_path / "open_retry"
    state_dir.mkdir(mode=0o755)
    monkeypatch.setenv("CLUXION_SUPERCODER_RETRY_DIR", str(state_dir))
    mode_before = stat.S_IMODE(state_dir.stat().st_mode)
    advice = retry_loop.record_failure("/ws", "a.py", "no_match", old_text="x")
    assert advice.attempt == 1
    assert retry_loop._failures
    assert stat.S_IMODE(state_dir.stat().st_mode) == mode_before
    assert list(state_dir.glob("*.json")) == []


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
