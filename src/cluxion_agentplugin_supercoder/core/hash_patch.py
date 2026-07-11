"""Hash-verified safe patch — ported from cluxion-os _hash_edit_core."""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
import threading
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from cluxion_agentplugin_supercoder import rust_bridge

try:
    import fcntl
except ImportError:
    fcntl = None

DEFAULT_FUZZY_THRESHOLD = 0.86
AMBIGUITY_MARGIN = 0.015
MAX_CONTEXT_SCAN = 8
MAX_LINE_DRIFT = 2

_thread_fallback_lock = threading.Lock()


def _lock_dir() -> Path:
    # UID-scoped so a shared /tmp entry cannot collide across users (mode 0700).
    return Path(tempfile.gettempdir()) / f"cluxion-supercoder-locks-{os.geteuid()}"


def _lock_path(path: Path) -> Path:
    # Same absolute target path -> same lock file, but outside the user tree:
    # patch runs used to leave .<name>.cluxion-lock litter in the workspace.
    digest = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:32]
    return _lock_dir() / f"{digest}.lock"


def _dir_open_flags() -> int | None:
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


def _ensure_lock_dir() -> Path:
    """Create the UID-scoped lock dir if missing; never chmod an existing path."""
    lock_dir = _lock_dir()
    try:
        os.makedirs(lock_dir, mode=0o700, exist_ok=True)
    except OSError:
        # Concurrent creator may have won; only fail if the path is still missing.
        if not lock_dir.is_dir():
            raise
    return lock_dir


@contextmanager
def _exclusive_lock(path: Path):
    """Exclusive advisory lock on a UID-scoped sidecar (fcntl.flock).

    Thread fallback is only used when fcntl itself is unavailable. When fcntl
    exists but race-honest directory/file open flags are missing, fail closed
    rather than opening a path-level fd that cannot refuse symlinks. A POSIX
    lock-dir/file that fails ownership/mode validation also raises rather than
    silently dropping interprocess exclusion or chmod'ing another owner's path.
    """
    if fcntl is None:
        with _thread_fallback_lock:
            yield
        return
    dir_flags = _dir_open_flags()
    file_flags = _file_open_flags(create=True)
    if dir_flags is None or file_flags is None:
        raise OSError("race-honest lock open flags unavailable (O_DIRECTORY/O_NOFOLLOW/O_CLOEXEC)")

    digest = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:32]
    basename = f"{digest}.lock"
    # openat(O_CREAT) can spuriously ENOENT under heavy concurrent creators on
    # some platforms; re-ensure the dir and retry without dropping flock exclusion.
    last_err: OSError | None = None
    for _ in range(5):
        lock_dir = _ensure_lock_dir()
        try:
            dir_fd = os.open(str(lock_dir), dir_flags)
        except OSError as exc:
            last_err = exc
            continue
        try:
            if not _dirfd_is_safe(dir_fd):
                raise OSError(f"unsafe lock directory: {lock_dir}")
            try:
                fd = os.open(basename, file_flags, 0o600, dir_fd=dir_fd)
            except FileNotFoundError as exc:
                last_err = exc
                continue
            try:
                if not _filefd_is_safe(fd):
                    raise OSError(f"unsafe lock file: {basename}")
                fcntl.flock(fd, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                return
            finally:
                os.close(fd)
        finally:
            os.close(dir_fd)
    assert last_err is not None
    raise last_err


@dataclass(frozen=True, slots=True)
class PatchResult:
    success: bool
    file_path: str
    strategy: str
    message: str
    expected_hash: str
    matched_hash: str | None = None
    similarity: float = 0.0
    replacements: int = 0
    pre_image_raw: str = ""
    post_hash: str = ""
    # Private/non-repr: exact bytes committed by _commit for syntax verdicts only.
    _post_image: str = field(default="", repr=False, compare=False)


def file_hash(content: str) -> str:
    return hashlib.sha256(_normalize_newlines(content).encode("utf-8")).hexdigest()


def hash_block(content: str, context_lines: int) -> str:
    normalized = _normalize_newlines(content)
    material = f"context_lines={context_lines}\0{normalized}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def apply_patch(
    path: Path,
    *,
    old_text: str,
    new_text: str,
    expected_file_hash: str = "",
    fuzzy_threshold: float = DEFAULT_FUZZY_THRESHOLD,
) -> PatchResult:
    if not old_text:
        return _failed(str(path), "empty_old_text", expected_file_hash, "old_text must be non-empty")
    old_text = _normalize_newlines(old_text)
    new_text = _normalize_newlines(new_text)
    if not path.exists() and not path.is_symlink():
        return _failed(str(path), "missing_file", expected_file_hash, "file not found")
    with _exclusive_lock(path):
        # Final-component symlink: refuse mutation (reads may follow; writes must not).
        # Recheck under the lock so a file→symlink swap before apply still fails closed.
        blocked = _symlink_patch_block(path, expected_file_hash)
        if blocked is not None:
            return blocked
        if not path.exists():
            return _failed(str(path), "missing_file", expected_file_hash, "file not found")
        with path.open(encoding="utf-8", newline="") as source:
            raw = source.read()
        eol = "\r\n" if "\r\n" in raw else "\n"
        text = _normalize_newlines(raw)
        current_hash = file_hash(text)
        if expected_file_hash and current_hash != _normalize_hash(expected_file_hash):
            return _failed(str(path), "stale_file", expected_file_hash, "file changed since cursor was created")
        exact = _exact_spans(text, old_text)
        if len(exact) > 1:
            return _failed(
                str(path),
                "ambiguous_exact",
                expected_file_hash or current_hash,
                f"old_text matches {len(exact)} locations; add surrounding context to disambiguate",
            )
        if exact:
            start, end = exact[0]
            return _commit(
                path,
                text,
                start,
                end,
                new_text,
                "exact",
                expected_file_hash or current_hash,
                current_hash,
                1.0,
                eol=eol,
                pre_image_raw=raw,
            )
        fuzzy = _fuzzy_span(text, old_text)
        if fuzzy and fuzzy[3] >= fuzzy_threshold and not fuzzy[4]:
            return _commit(
                path,
                text,
                fuzzy[0],
                fuzzy[1],
                new_text,
                "fuzzy",
                expected_file_hash or current_hash,
                current_hash,
                fuzzy[3],
                eol=eol,
                pre_image_raw=raw,
            )
        return _failed(str(path), "no_match", expected_file_hash or current_hash, "patch target not found")


def _symlink_patch_block(path: Path, expected_file_hash: str) -> PatchResult | None:
    """Reject patching when the final path component is a symlink."""
    if not path.is_symlink():
        return None
    try:
        real_hint = os.readlink(path)
    except OSError:
        real_hint = str(path)
    return _failed(
        str(path),
        "symlink_patch_blocked",
        expected_file_hash,
        f"refusing to patch symlink; real path hint: {real_hint}",
    )


def _normalize_newlines(content: str) -> str:
    return content.replace("\r\n", "\n").replace("\r", "\n")


def _normalize_hash(value: str) -> str:
    raw = value.strip().lower()
    if raw.startswith("sha256:"):
        raw = raw.removeprefix("sha256:")
    if len(raw) != 64:
        raise ValueError("hash must be 64-char sha256")
    return raw


def _exact_spans(text: str, needle: str) -> list[tuple[int, int]]:
    if not needle:
        return []
    spans: list[tuple[int, int]] = []
    offset = 0
    while True:
        start = text.find(needle, offset)
        if start < 0:
            return spans
        spans.append((start, start + len(needle)))
        offset = start + len(needle)


def _candidate_spans(text: str, reference: str, line_drift: int) -> list[tuple[int, int, str, int, int]]:
    """Yield (start, end, block, start_line, end_line) — the line range comes
    free from the enumeration, so consumers never rescan offsets per span."""
    if not reference:
        return []
    lines = text.splitlines(keepends=True)
    if not lines:
        return []
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    target = max(1, len(reference.splitlines(keepends=True)))
    lower = max(1, target - line_drift)
    upper = min(len(lines), target + line_drift)
    spans: list[tuple[int, int, str, int, int]] = []
    for width in range(lower, upper + 1):
        for start_line in range(0, len(lines) - width + 1):
            start = offsets[start_line]
            end = offsets[start_line + width]
            block = text[start:end]
            spans.append((start, end, block, start_line, start_line + width))
    return spans


def _fuzzy_span(text: str, reference: str) -> tuple[int, int, str, float, bool] | None:
    """Fuzzy tier routing: rust backend when available, python otherwise.

    The rust op returns code-point offsets, so text[start:end] recovers the
    matched block exactly as _best_fuzzy_span would have produced it.
    """
    if reference:
        native = rust_bridge.fuzzy_span_result(text, reference)
        if native is not None:
            # fuzzy_span_result already validated types/ranges; no coerce here.
            if native.get("matched") is not True:
                return None
            start = native["start"]
            end = native["end"]
            return (
                int(start),
                int(end),
                text[int(start) : int(end)],
                float(native["score"]),
                native["ambiguous"] is True,
            )
    return _best_fuzzy_span(text, reference)


def _best_fuzzy_span(text: str, reference: str) -> tuple[int, int, str, float, bool] | None:
    if not reference:
        return None
    best: tuple[int, int, str, float] | None = None
    best_lines: tuple[int, int] | None = None
    # Ambiguity must be decided after the full scan: judging against the
    # running best is order-dependent (a later, better match would reset the
    # flag and silently apply). Collect every candidate that clears the fuzzy
    # threshold, then compare against the final winner.
    contenders: list[tuple[float, int, int]] = []
    sm = SequenceMatcher(autojunk=False)
    sm.set_seq2(reference)
    for start, end, block, start_line, end_line in _candidate_spans(text, reference, MAX_LINE_DRIFT):
        sm.set_seq1(block)
        if best is not None:
            prune_below = best[3] - AMBIGUITY_MARGIN
            if sm.real_quick_ratio() < prune_below or sm.quick_ratio() < prune_below:
                continue
        score = sm.ratio()
        if best is None or score > best[3]:
            best = (start, end, block, score)
            best_lines = (start_line, end_line)
        if score >= DEFAULT_FUZZY_THRESHOLD:
            contenders.append((score, start_line, end_line))
    if best is None or best_lines is None:
        return None
    # ambiguous iff a genuinely different (non-overlapping) location scores within
    # the margin of the final winner; the winner overlaps itself, so it never counts
    ambiguous = any(
        best[3] - score < AMBIGUITY_MARGIN and (end_line <= best_lines[0] or start_line >= best_lines[1])
        for score, start_line, end_line in contenders
    )
    return best[0], best[1], best[2], best[3], ambiguous


def _atomic_write(path: Path, content: str) -> None:
    """Atomic replace via temp in same dir + fsync to prevent corruption on crash."""
    dir_ = path.parent
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="", dir=dir_, delete=False, suffix=".tmp"
    ) as tmp:
        tmp.write(content)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)
    with suppress(OSError):
        os.chmod(tmp_path, os.stat(path).st_mode & 0o777)
    os.replace(tmp_path, path)


def revert_if_unchanged(path: Path, pre_image_raw: str, expected_post_hash: str) -> bool:
    with _exclusive_lock(path):
        try:
            with path.open(encoding="utf-8", newline="") as source:
                current_raw = source.read()
        except (OSError, UnicodeDecodeError):
            return False
        if file_hash(current_raw) != expected_post_hash:
            return False
        _atomic_write(path, pre_image_raw)
        return True


def _commit(
    path: Path,
    text: str,
    start: int,
    end: int,
    new_content: str,
    strategy: str,
    expected: str,
    matched: str,
    score: float,
    *,
    eol: str = "\n",
    pre_image_raw: str,
) -> PatchResult:
    # Re-check under the held exclusive lock: a TOCTOU swap to a symlink must
    # not let _atomic_write follow the link and clobber an external target.
    blocked = _symlink_patch_block(path, expected)
    if blocked is not None:
        return blocked
    updated = f"{text[:start]}{new_content}{text[end:]}"
    if eol != "\n":
        updated = updated.replace("\n", eol)
    _atomic_write(path, updated)
    return PatchResult(
        True,
        str(path),
        strategy,
        "patch applied",
        expected,
        matched,
        round(score, 4),
        1,
        pre_image_raw,
        file_hash(updated),
        _post_image=updated,
    )


def _failed(path: str, strategy: str, expected: str, message: str, score: float = 0.0) -> PatchResult:
    return PatchResult(False, path, strategy, message, expected, None, round(score, 4), 0)


__all__ = ["PatchResult", "apply_patch", "file_hash", "hash_block", "revert_if_unchanged"]
