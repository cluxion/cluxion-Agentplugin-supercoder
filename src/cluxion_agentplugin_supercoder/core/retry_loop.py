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
import stat
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
    "syntax_rejected": (
        "The patch was rejected before writing because the candidate did not parse. Check "
        "syntax_errors for the exact line, then fix brackets/indentation in new_text before retrying."
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
    _disk_delete_basename(_state_basename(workspace, path))


def reset() -> None:
    """Drop all tracked state (test isolation)."""
    _failures.clear()
    _disk_reset()


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
    basename = _state_basename(workspace, path)
    try:
        with _held_state_dirfd() as dir_fd:
            if dir_fd is None:
                return None
            history = _append_via_dirfd(dir_fd, basename, signature)
            _prune_via_dirfd(dir_fd)
            return history
    except OSError:
        return None


def _disk_delete_basename(basename: str) -> None:
    try:
        with _held_state_dirfd() as dir_fd:
            if dir_fd is None:
                return
            _unlink_regular_state_file(dir_fd, basename)
    except OSError:
        return


def _disk_reset() -> None:
    try:
        with _held_state_dirfd() as dir_fd:
            if dir_fd is None:
                return
            for name in _list_state_basenames(dir_fd):
                _unlink_regular_state_file(dir_fd, name)
    except OSError:
        return


def _dir_open_flags() -> int | None:
    """Return open flags only when the full race-honest set is available."""
    required = ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC")
    if not all(hasattr(os, name) for name in required):
        return None
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _file_open_flags(create: bool) -> int | None:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_CLOEXEC"):
        return None
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC
    if create:
        flags |= os.O_CREAT
    return flags


@contextmanager
def _held_state_dirfd():
    """Open the state directory once and hold the dirfd for the operation.

    On unsupported platforms, symlink/non-dir/unsafe owner/mode, yield None so
    callers use the memory fallback and never chmod/delete/alter the path.
    """
    flags = _dir_open_flags()
    if flags is None:
        yield None
        return
    state_dir = _state_dir()
    try:
        # Create only when missing; never chmod an existing unsafe path.
        if not state_dir.exists():
            state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError:
        yield None
        return
    try:
        dir_fd = os.open(str(state_dir), flags)
    except OSError:
        yield None
        return
    try:
        if not _dirfd_is_safe(dir_fd):
            yield None
            return
        yield dir_fd
    finally:
        os.close(dir_fd)


def _dirfd_is_safe(dir_fd: int) -> bool:
    try:
        st = os.fstat(dir_fd)
    except OSError:
        return False
    if not stat.S_ISDIR(st.st_mode):
        return False
    if st.st_uid != os.geteuid():
        return False
    return stat.S_IMODE(st.st_mode) == 0o700


def _filefd_is_safe(fd: int) -> bool:
    try:
        st = os.fstat(fd)
    except OSError:
        return False
    if not stat.S_ISREG(st.st_mode):
        return False
    if st.st_uid != os.geteuid():
        return False
    return stat.S_IMODE(st.st_mode) == 0o600


def _append_via_dirfd(dir_fd: int, basename: str, signature: str) -> list[str]:
    flags = _file_open_flags(create=True)
    if flags is None:
        raise OSError("O_NOFOLLOW unavailable")
    fd = os.open(basename, flags, 0o600, dir_fd=dir_fd)
    try:
        if not _filefd_is_safe(fd):
            raise OSError("unsafe retry state file")
        with os.fdopen(fd, "r+", encoding="utf-8", closefd=False) as handle, _locked(fd):
            history = _decode(handle.read())
            history.append(signature)
            handle.seek(0)
            handle.truncate()
            json.dump({"updated": time.time(), "history": history}, handle)
            handle.flush()
        return history
    finally:
        os.close(fd)


def _unlink_regular_state_file(dir_fd: int, basename: str) -> None:
    if not basename.endswith(".json") or "/" in basename or basename in {".", ".."}:
        return
    flags = _file_open_flags(create=False)
    if flags is None:
        return
    try:
        fd = os.open(basename, flags, dir_fd=dir_fd)
    except FileNotFoundError:
        return
    except OSError:
        return
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            return
        if opened.st_uid != os.geteuid() or stat.S_IMODE(opened.st_mode) != 0o600:
            return
        current = os.stat(basename, dir_fd=dir_fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
            return
        os.unlink(basename, dir_fd=dir_fd)
    except (FileNotFoundError, OSError):
        return
    finally:
        os.close(fd)


def _list_state_basenames(dir_fd: int) -> list[str]:
    # Enumerate via the held dirfd so a path-string race cannot redirect us.
    try:
        return [name for name in os.listdir(dir_fd) if name.endswith(".json")]
    except OSError:
        return []


def _prune_via_dirfd(dir_fd: int) -> None:
    # ponytail: full-dir mtime sort on every failure; fine at the 256-file cap.
    try:
        dated: list[tuple[float, str]] = []
        for name in _list_state_basenames(dir_fd):
            try:
                st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            if st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o600:
                continue
            dated.append((st.st_mtime, name))
        dated.sort()
        for _, name in dated[:-MAX_TRACKED_FILES]:
            _unlink_regular_state_file(dir_fd, name)
    except OSError:
        return


def _decode(raw: str) -> list[str]:
    try:
        state = json.loads(raw)
        if time.time() - float(state["updated"]) > STATE_TTL_SECONDS:
            return []
        return [str(item) for item in state["history"]]
    except (KeyError, TypeError, ValueError):
        return []


def _state_dir() -> Path:
    override = os.environ.get("CLUXION_SUPERCODER_RETRY_DIR", "").strip()
    return Path(override) if override else Path(tempfile.gettempdir()) / "cluxion-supercoder-retry"


def _state_basename(workspace: str, path: str) -> str:
    digest = hashlib.sha256(f"{workspace}\0{path}".encode()).hexdigest()[:32]
    return f"{digest}.json"


def _state_path(workspace: str, path: str) -> Path:
    """Path form kept for tests that inspect the on-disk state file location."""
    return _state_dir() / _state_basename(workspace, path)


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
