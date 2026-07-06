"""L4 retry guidance: track patch failures per file and tell the host
model what to do differently on the next attempt.

The plugin cannot author a new patch itself — the host model does — so
the correction loop is closed by feedback: every failure returns an
attempt counter, a failure-specific instruction, and an escalation flag
once retries stop making progress. Failure history lives in a small
flock-guarded JSON file per (workspace, path) under the system temp dir,
so the attempt budget survives one-shot CLI invocations; entries expire
after STATE_TTL_SECONDS and are cleared on success. If the state dir is
unusable the tracker degrades to the old in-process LRU dict.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

try:
    import fcntl
except ImportError:
    fcntl = None

MAX_ATTEMPTS = 3
MAX_TRACKED_FILES = 256
STATE_TTL_SECONDS = 900

_GUIDANCE = {
    "no_match": (
        "old_text was not found in the file. Re-read the target region with "
        "supercoder_read_window and copy old_text exactly from the result."
    ),
    "stale_file": (
        "The file changed after the cursor was created. Rebuild the cursor "
        "with supercoder_read_window or supercoder_cursor_map and use the "
        "fresh file hash."
    ),
    "syntax_reverted": (
        "The patch was rolled back because the file no longer parsed. Check "
        "syntax_errors for the exact line, then fix brackets/indentation in "
        "new_text before retrying."
    ),
    "missing_file": ("The target file does not exist. Verify the path against supercoder_cursor_map before retrying."),
}
_REPEAT_GUIDANCE = (
    "This is the same attempt as before (identical old_text and failure). "
    "Do not resend it — change the input: re-read the file and rebuild the "
    "patch from current content."
)
_ESCALATE_GUIDANCE = (
    "Retry budget exhausted for this file. Stop patching: re-plan with a "
    "smaller edit, or rewrite the enclosing block in a single larger patch."
)


@dataclass(frozen=True)
class RetryAdvice:
    attempt: int
    max_attempts: int
    repeated_input: bool
    escalate: bool
    guidance: str

    def to_payload(self) -> dict[str, object]:
        return {
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "repeated_input": self.repeated_input,
            "escalate": self.escalate,
            "guidance": self.guidance,
        }


# In-process fallback, used only when the on-disk state dir is unusable.
_failures: OrderedDict[tuple[str, str], list[str]] = OrderedDict()
_thread_fallback_lock = threading.Lock()


def record_failure(workspace: str, path: str, reason: str, *, old_text: str = "") -> RetryAdvice:
    """Record a patch failure and return guidance for the next attempt."""
    signature = f"{reason}:{_digest(old_text)}"
    history = _disk_append(workspace, path, signature)
    if history is None:
        history = _memory_append(workspace, path, signature)
    attempt = len(history)
    repeated = attempt > 1 and history[-2] == signature
    escalate = attempt >= MAX_ATTEMPTS
    if escalate:
        guidance = _ESCALATE_GUIDANCE
    elif repeated:
        guidance = _REPEAT_GUIDANCE
    else:
        guidance = _GUIDANCE.get(reason, "Re-read the file and rebuild the patch from current content.")
    return RetryAdvice(attempt, MAX_ATTEMPTS, repeated, escalate, guidance)


def record_success(workspace: str, path: str) -> None:
    """Clear the failure history once a patch lands."""
    _failures.pop((workspace, path), None)
    try:
        _state_path(workspace, path).unlink(missing_ok=True)
    except OSError:
        pass


def reset() -> None:
    """Drop all tracked state (test isolation)."""
    _failures.clear()
    try:
        for entry in _state_dir().glob("*.json"):
            entry.unlink(missing_ok=True)
    except OSError:
        pass


def _memory_append(workspace: str, path: str, signature: str) -> list[str]:
    key = (workspace, path)
    history = _failures.pop(key, [])
    history.append(signature)
    _failures[key] = history
    while len(_failures) > MAX_TRACKED_FILES:
        _failures.popitem(last=False)
    return history


def _disk_append(workspace: str, path: str, signature: str) -> list[str] | None:
    """Append to the persistent history; None when the state dir is unusable."""
    try:
        state_dir = _state_dir()
        state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(str(_state_path(workspace, path)), os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, "r+", encoding="utf-8") as handle, _locked(fd):
            history = _decode(handle.read())
            history.append(signature)
            handle.seek(0)
            handle.truncate()
            json.dump({"updated": time.time(), "history": history}, handle)
            handle.flush()
        _prune(state_dir)
        return history
    except OSError:
        return None


def _decode(raw: str) -> list[str]:
    try:
        state = json.loads(raw)
        if time.time() - float(state["updated"]) > STATE_TTL_SECONDS:
            return []
        return [str(item) for item in state["history"]]
    except (KeyError, TypeError, ValueError):
        return []


def _prune(state_dir: Path) -> None:
    # ponytail: full-dir mtime sort on every failure; fine at the 256-file cap.
    try:
        entries = sorted(state_dir.glob("*.json"), key=lambda entry: entry.stat().st_mtime)
        for stale in entries[:-MAX_TRACKED_FILES]:
            stale.unlink(missing_ok=True)
    except OSError:
        pass


def _state_dir() -> Path:
    override = os.environ.get("CLUXION_SUPERCODER_RETRY_DIR", "").strip()
    return Path(override) if override else Path(tempfile.gettempdir()) / "cluxion-supercoder-retry"


def _state_path(workspace: str, path: str) -> Path:
    digest = hashlib.sha256(f"{workspace}\0{path}".encode()).hexdigest()[:32]
    return _state_dir() / f"{digest}.json"


@contextmanager
def _locked(fd: int):
    """Exclusive advisory flock; thread-lock fallback on non-POSIX (mirrors hash_patch)."""
    if fcntl is None:
        with _thread_fallback_lock:
            yield
        return
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


__all__ = ["MAX_ATTEMPTS", "STATE_TTL_SECONDS", "RetryAdvice", "record_failure", "record_success", "reset"]
