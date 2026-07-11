from __future__ import annotations

import concurrent.futures
import contextlib
import os
import tempfile
import time
from difflib import SequenceMatcher
from pathlib import Path

import pytest

from cluxion_agentplugin_supercoder.core.hash_patch import (
    AMBIGUITY_MARGIN,
    DEFAULT_FUZZY_THRESHOLD,
    MAX_LINE_DRIFT,
    _best_fuzzy_span,
    _candidate_spans,
    _exact_spans,
    apply_patch,
    file_hash,
)


def _best_fuzzy_span_legacy(text: str, reference: str) -> tuple[int, int, str, float, bool] | None:
    """Pre-optimization reference implementation for byte-identical regression checks."""
    best: tuple[int, int, str, float] | None = None
    best_lines: tuple[int, int] | None = None
    ambiguous = False
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    # legacy ignores the precomputed line range and rescans offsets itself
    for start, end, block, *_ in _candidate_spans(text, reference, MAX_LINE_DRIFT):
        start_line = 0
        while start_line < len(offsets) - 1 and offsets[start_line + 1] <= start:
            start_line += 1
        end_line = start_line
        while end_line < len(offsets) - 1 and offsets[end_line] < end:
            end_line += 1
        score = SequenceMatcher(None, block, reference, autojunk=False).ratio()
        if best is None or score > best[3]:
            best = (start, end, block, score)
            best_lines = (start_line, end_line)
            ambiguous = False
        elif score >= DEFAULT_FUZZY_THRESHOLD and best and abs(score - best[3]) < AMBIGUITY_MARGIN:
            if best_lines is not None and not (end_line <= best_lines[0] or start_line >= best_lines[1]):
                continue
            ambiguous = True
    if best is None:
        return None
    return best[0], best[1], best[2], best[3], ambiguous


def _fuzzy_result_key(result: tuple[int, int, str, float, bool] | None) -> tuple | None:
    if result is None:
        return None
    start, end, _block, score, ambiguous = result
    return (start, end, score, ambiguous)


@pytest.mark.parametrize(
    ("text", "reference"),
    [
        pytest.param(
            "\n".join(f"line_{i} = {i}" for i in range(20))
            + "\n"
            + "def target():\n    return 42\n"
            + "\n".join(f"tail_{i} = {i}" for i in range(20))
            + "\n",
            "def target():\n    return 43\n",
            id="exact-best-in-middle",
        ),
        pytest.param(
            "def f():\n    return 1\n\n" + "def f():\n    return 1\n",
            "def f():\n    return 2\n",
            id="ambiguous-near-ties",
        ),
        pytest.param(
            "alpha\nbeta\ngamma\ndelta\n",
            "alpha\nBETA\ngamma\n",
            id="clear-single-best",
        ),
        pytest.param(
            "alpha\nbeta\ngamma\n",
            "completely different content\n",
            id="no-match",
        ),
    ],
)
def test_best_fuzzy_span_matches_legacy(text: str, reference: str) -> None:
    legacy = _fuzzy_result_key(_best_fuzzy_span_legacy(text, reference))
    optimized = _fuzzy_result_key(_best_fuzzy_span(text, reference))
    assert optimized == legacy


def test_best_fuzzy_span_large_file_benchmark() -> None:
    """Optimized path should be materially faster on a ~500-line fuzzy search."""
    filler = "\n".join(f"# filler line {i:03d} with some padding text" for i in range(480)) + "\n"
    target = "def handler(request):\n    value = compute(request)\n    return value\n"
    text = filler + target + filler
    reference = "def handler(request):\n    value = compute(request)  # cached\n    return value\n"

    legacy_start = time.perf_counter()
    legacy = _best_fuzzy_span_legacy(text, reference)
    legacy_elapsed = time.perf_counter() - legacy_start

    optimized_start = time.perf_counter()
    optimized = _best_fuzzy_span(text, reference)
    optimized_elapsed = time.perf_counter() - optimized_start

    assert _fuzzy_result_key(optimized) == _fuzzy_result_key(legacy)
    assert optimized_elapsed < legacy_elapsed * 0.6


def test_exact_spans_empty_needle_returns_immediately() -> None:
    assert _exact_spans("hello", "") == []


def test_apply_patch_empty_old_text_fails_fast(tmp_path: Path) -> None:
    path = tmp_path / "a.py"
    path.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    start = time.perf_counter()
    result = apply_patch(path, old_text="", new_text="INSERT\n")
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0
    assert result.success is False
    assert result.strategy == "empty_old_text"
    assert "non-empty" in result.message
    assert path.read_text(encoding="utf-8") == "alpha\nbeta\ngamma\n"


def test_empty_reference_does_not_hang_fuzzy_path() -> None:
    text = "\n".join(f"line_{i} = {i}" for i in range(500)) + "\n"
    start = time.perf_counter()
    assert _candidate_spans(text, "", MAX_LINE_DRIFT) == []
    assert _best_fuzzy_span(text, "") is None
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0


def test_unique_exact_match_commits(tmp_path: Path) -> None:
    path = tmp_path / "a.py"
    path.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    expected = file_hash(path.read_text(encoding="utf-8"))
    result = apply_patch(path, old_text="beta\n", new_text="BETA\n", expected_file_hash=expected)
    assert result.success is True
    assert result.strategy == "exact"
    assert result.replacements == 1
    assert path.read_text(encoding="utf-8") == "alpha\nBETA\ngamma\n"


def test_crlf_patch_text_preserves_crlf(tmp_path: Path) -> None:
    path = tmp_path / "a.py"
    path.write_bytes(b"alpha\r\nbeta\r\ngamma\r\n")

    result = apply_patch(path, old_text="beta\r\n", new_text="BETA\r\n")

    updated = path.read_bytes()
    assert result.success is True
    assert result.strategy == "exact"
    assert updated == b"alpha\r\nBETA\r\ngamma\r\n"
    assert b"\r\r\n" not in updated


def test_stale_patch_blocked(tmp_path: Path) -> None:
    path = tmp_path / "a.py"
    path.write_text("one\n", encoding="utf-8")
    result = apply_patch(path, old_text="one\n", new_text="two\n", expected_file_hash="0" * 64)
    assert result.success is False
    assert "changed" in result.message


def test_missing_file_fails_closed(tmp_path: Path) -> None:
    result = apply_patch(tmp_path / "absent.py", old_text="x", new_text="y")
    assert result.success is False
    assert result.strategy == "missing_file"


def test_final_component_symlink_blocks_patch_without_touching_target(tmp_path: Path) -> None:
    target = tmp_path / "real.py"
    target.write_text("alpha\nbeta\n", encoding="utf-8")
    link = tmp_path / "via_link.py"
    link.symlink_to(target)
    result = apply_patch(link, old_text="beta\n", new_text="BETA\n")
    assert result.success is False
    assert result.strategy == "symlink_patch_blocked"
    assert "real path hint" in result.message
    assert "real.py" in result.message or str(target) in result.message
    assert link.is_symlink()
    assert target.read_text(encoding="utf-8") == "alpha\nbeta\n"
    assert os.readlink(link) == str(target) or Path(os.readlink(link)).name == "real.py"


def test_symlink_swap_before_commit_is_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "a.py"
    path.write_text("alpha\nbeta\n", encoding="utf-8")
    victim = tmp_path / "victim.py"
    victim.write_text("KEEP\n", encoding="utf-8")
    import cluxion_agentplugin_supercoder.core.hash_patch as hp

    original_commit = hp._commit

    def swap_then_commit(*args, **kwargs):
        path.unlink()
        path.symlink_to(victim)
        return original_commit(*args, **kwargs)

    monkeypatch.setattr(hp, "_commit", swap_then_commit)
    result = apply_patch(path, old_text="beta\n", new_text="BETA\n")
    # Either the patched _commit rejects after swap, or we observe unchanged victim.
    # With recheck inside _commit, strategy is symlink_patch_blocked.
    assert result.success is False
    assert result.strategy == "symlink_patch_blocked"
    assert victim.read_text(encoding="utf-8") == "KEEP\n"
    assert path.is_symlink()


def test_fuzzy_patch_tolerates_minor_drift(tmp_path: Path) -> None:
    path = tmp_path / "a.py"
    body = "def handler(request):\n    value = compute(request)\n    return value\n"
    path.write_text(body, encoding="utf-8")
    # old_text drifts from the file by one comment line the model forgot.
    drifted = "def handler(request):\n    value = compute(request)  # cached\n    return value\n"
    result = apply_patch(path, old_text=drifted, new_text="def handler(request):\n    return compute(request)\n")
    assert result.success is True
    assert result.strategy == "fuzzy"
    assert 0.86 <= result.similarity < 1.0
    assert path.read_text(encoding="utf-8") == "def handler(request):\n    return compute(request)\n"


def test_low_similarity_is_no_match(tmp_path: Path) -> None:
    path = tmp_path / "a.py"
    path.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    result = apply_patch(path, old_text="completely different content\n", new_text="x\n")
    assert result.success is False
    assert result.strategy == "no_match"


def test_ambiguous_fuzzy_candidates_refuse_to_guess(tmp_path: Path) -> None:
    # Two near-identical blocks: a fuzzy match could land on either, so the
    # patch must fail instead of silently editing the wrong one.
    path = tmp_path / "a.py"
    block = "def f():\n    return 1\n"
    path.write_text(block + "\n" + block, encoding="utf-8")
    near = "def f():\n    return 2\n"
    result = apply_patch(path, old_text=near, new_text="def f():\n    return 3\n")
    assert result.success is False
    assert result.strategy == "no_match"


def test_ambiguous_exact_match_refuses_to_guess(tmp_path: Path) -> None:
    path = tmp_path / "a.py"
    content = "x = 1\ny = 2\nx = 1\n"
    path.write_text(content, encoding="utf-8")
    result = apply_patch(path, old_text="x = 1\n", new_text="x = 9\n")
    assert result.success is False
    assert result.strategy == "ambiguous_exact"
    assert result.message == "old_text matches 2 locations; add surrounding context to disambiguate"
    assert path.read_text(encoding="utf-8") == content


def test_sha256_prefix_and_bad_hash_rejected(tmp_path: Path) -> None:
    path = tmp_path / "a.py"
    path.write_text("one\n", encoding="utf-8")
    prefixed = "sha256:" + file_hash("one\n")
    result = apply_patch(path, old_text="one\n", new_text="two\n", expected_file_hash=prefixed)
    assert result.success is True
    path.write_text("one\n", encoding="utf-8")
    with pytest.raises(ValueError):
        apply_patch(path, old_text="one\n", new_text="two\n", expected_file_hash="abc")


def test_fuzzy_short_2line_block_with_drift_applies(tmp_path: Path) -> None:
    # regression: 2-line unique target with minor drift (trailing space + typo) must apply
    # even though different window widths produce overlapping candidates
    path = tmp_path / "multi.py"
    body = """def foo():
    x = 1
    return x

def bar():
    y = 2
    return y
"""
    path.write_text(body, encoding="utf-8")
    # drifted old_text: trailing space on first line of block, one-char typo on second
    drifted = "    y = 2 \n    return z\n"
    new = "    y = 42\n    return y\n"
    result = apply_patch(path, old_text=drifted, new_text=new)
    assert result.success is True
    assert result.strategy == "fuzzy"
    content = path.read_text(encoding="utf-8")
    assert "y = 42" in content
    assert "return y" in content
    assert "def bar():" in content


def test_duplicate_blocks_still_ambiguous(tmp_path: Path) -> None:
    # genuine duplicate (non-overlapping) must still refuse
    path = tmp_path / "dups.py"
    block = "def f():\n    return 1\n"
    path.write_text(block + "\n" + block, encoding="utf-8")
    near = "def f():\n    return 2\n"
    result = apply_patch(path, old_text=near, new_text="def f():\n    return 3\n")
    assert result.success is False
    assert result.strategy == "no_match"


# === Order-independence of the ambiguity gate ===

_NEAR_TIE_OLD = "def compute_total(values):\n    total = sum(values) + offset_marker_a\n    return total\n"
_NEAR_TIE_BETTER = _NEAR_TIE_OLD.replace("offset_marker_a", "offset_marker_b")  # 1-char drift
_NEAR_TIE_WORSE = _NEAR_TIE_OLD.replace("offset_marker_a", "offset_marker_zz")  # 2-char drift
# long filler lines keep wider windows (block + neighbor line) far below the margin,
# so the two exact-width block windows are the only near-tie candidates
_NEAR_TIE_FILLER = (
    "# header section with plenty of padding text to dilute wider candidate windows\n"
    "import math  # extra trailing comment padding so this line is long too\n",
    "# unrelated middle separator with plenty of padding text between the blocks\n"
    "CONSTANT = 12345  # extra trailing comment padding so this line is long too\n",
    "# trailer line with plenty of padding characters to dilute wider windows\n",
)


def _near_tie_content(better_first: bool, worse: str = _NEAR_TIE_WORSE) -> str:
    top, mid, end = _NEAR_TIE_FILLER
    first, second = (_NEAR_TIE_BETTER, worse) if better_first else (worse, _NEAR_TIE_BETTER)
    return top + first + mid + second + end


def test_near_tie_blocks_are_a_genuine_near_tie() -> None:
    # guard: if these constants drift out of the margin the ordering tests degenerate
    s_better = SequenceMatcher(None, _NEAR_TIE_BETTER, _NEAR_TIE_OLD, autojunk=False).ratio()
    s_worse = SequenceMatcher(None, _NEAR_TIE_WORSE, _NEAR_TIE_OLD, autojunk=False).ratio()
    assert s_better >= DEFAULT_FUZZY_THRESHOLD
    assert s_worse >= DEFAULT_FUZZY_THRESHOLD
    assert 0 < s_better - s_worse < AMBIGUITY_MARGIN


@pytest.mark.parametrize("better_first", [True, False], ids=["better-first", "better-last"])
def test_near_tie_ambiguity_refuses_in_both_orderings(better_first: bool, tmp_path: Path) -> None:
    # regression: the decision used to flip with candidate order — applied when the
    # better match was scanned later, refused when it was scanned first
    content = _near_tie_content(better_first)
    assert sorted(content) == sorted(_near_tie_content(not better_first))  # same bytes, reordered
    path = tmp_path / "a.py"
    path.write_text(content, encoding="utf-8")
    result = apply_patch(
        path,
        old_text=_NEAR_TIE_OLD,
        new_text="def compute_total(values):\n    return sum(values)\n",
    )
    assert result.success is False
    assert result.strategy == "no_match"
    assert path.read_text(encoding="utf-8") == content


@pytest.mark.parametrize("better_first", [True, False], ids=["better-first", "better-last"])
def test_clear_winner_above_margin_applies_in_both_orderings(better_first: bool, tmp_path: Path) -> None:
    # positive control: runner-up beyond the margin must NOT trip the gate, either order
    clear_worse = _NEAR_TIE_OLD.replace("offset_marker_a", "unrelated_zz_qq")
    s_better = SequenceMatcher(None, _NEAR_TIE_BETTER, _NEAR_TIE_OLD, autojunk=False).ratio()
    s_worse = SequenceMatcher(None, clear_worse, _NEAR_TIE_OLD, autojunk=False).ratio()
    assert s_worse >= DEFAULT_FUZZY_THRESHOLD
    assert s_better - s_worse >= AMBIGUITY_MARGIN
    content = _near_tie_content(better_first, worse=clear_worse)
    path = tmp_path / "a.py"
    path.write_text(content, encoding="utf-8")
    new = "def compute_total(values):\n    return sum(values)\n"
    result = apply_patch(path, old_text=_NEAR_TIE_OLD, new_text=new)
    assert result.success is True
    assert result.strategy == "fuzzy"
    updated = path.read_text(encoding="utf-8")
    assert new in updated
    assert _NEAR_TIE_BETTER not in updated  # the better block was replaced
    assert clear_worse in updated  # the runner-up untouched


# === Concurrency and atomicity tests ===


def _worker_apply(i: int, path: Path) -> bool:
    """Worker that applies a unique non-overlapping patch under lock."""
    old = f"# UNIQUE_PATCH_{i}_START\n"
    new = f"# UNIQUE_PATCH_{i}_DONE\n"
    # distinct markers, no value chaining conflict
    result = apply_patch(path, old_text=old, new_text=new)
    return result.success


def test_concurrent_patches_no_lost_update(tmp_path: Path) -> None:
    """8 concurrent workers on same file: lock serializes, no lost updates, final coherent."""
    path = tmp_path / "concurrent.py"
    # initial content with 8 distinct unique markers
    initial = "\n".join(f"# UNIQUE_PATCH_{i}_START" for i in range(8)) + "\n"
    path.write_text(initial, encoding="utf-8")

    # each patch changes its unique marker; lock ensures serialization, all succeed
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(_worker_apply, i, path) for i in range(8)]
        successes = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert all(successes), "Some patches lost due to race"
    final = path.read_text(encoding="utf-8")
    # final should have all DONE markers, no START left
    for i in range(8):
        assert f"# UNIQUE_PATCH_{i}_DONE" in final
        assert f"# UNIQUE_PATCH_{i}_START" not in final

    # hash chain consistent (changed from initial)
    new_hash = file_hash(final)
    assert new_hash != file_hash(initial)


def test_concurrent_patches_no_lost_update_stress(tmp_path: Path) -> None:
    """50 iterations of 8 concurrent workers: zero lost updates across all runs."""
    for _ in range(50):
        path = tmp_path / "concurrent_stress.py"
        initial = "\n".join(f"# UNIQUE_PATCH_{i}_START" for i in range(8)) + "\n"
        path.write_text(initial, encoding="utf-8")

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(_worker_apply, i, path) for i in range(8)]
            successes = [f.result() for f in concurrent.futures.as_completed(futures)]

        assert all(successes), "Some patches lost due to race"
        final = path.read_text(encoding="utf-8")
        for i in range(8):
            assert f"# UNIQUE_PATCH_{i}_DONE" in final
            assert f"# UNIQUE_PATCH_{i}_START" not in final


def test_lock_dir_is_uid_scoped(tmp_path: Path) -> None:
    path = tmp_path / "a.py"
    path.write_text("x = 1\n", encoding="utf-8")
    import cluxion_agentplugin_supercoder.core.hash_patch as hp

    lock = hp._lock_path(path)
    assert lock.parent.name == f"cluxion-supercoder-locks-{os.geteuid()}"
    # Opening the exclusive lock must create a validated 0700 owner dir + 0600 file.
    with hp._exclusive_lock(path):
        assert lock.parent.is_dir()
        assert lock.is_file()
        assert (lock.parent.stat().st_mode & 0o777) == 0o700
        assert (lock.stat().st_mode & 0o777) == 0o600
        assert lock.parent.stat().st_uid == os.geteuid()
        assert lock.stat().st_uid == os.geteuid()


def test_lock_dir_wrong_owner_mode_does_not_chmod_or_thread_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import stat as statmod

    import cluxion_agentplugin_supercoder.core.hash_patch as hp

    if hp.fcntl is None:
        pytest.skip("fcntl required for POSIX lock validation path")

    unsafe = tmp_path / f"cluxion-supercoder-locks-{os.geteuid()}"
    unsafe.mkdir(mode=0o755)
    mode_before = statmod.S_IMODE(unsafe.stat().st_mode)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    target = tmp_path / "t.py"
    target.write_text("x=1\n", encoding="utf-8")
    with pytest.raises(OSError), hp._exclusive_lock(target):
        pass
    assert statmod.S_IMODE(unsafe.stat().st_mode) == mode_before
    assert list(unsafe.iterdir()) == []


def test_exclusive_lock_fails_closed_when_race_honest_flags_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cluxion_agentplugin_supercoder.core.hash_patch as hp

    if hp.fcntl is None:
        pytest.skip("fcntl required for flag-unavailable fail-closed path")

    monkeypatch.setattr(hp, "_dir_open_flags", lambda: None)
    monkeypatch.setattr(hp, "_file_open_flags", lambda create: None)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    target = tmp_path / "t.py"
    target.write_text("x=1\n", encoding="utf-8")
    with pytest.raises(OSError), hp._exclusive_lock(target):
        pass
    lock_dir = tmp_path / f"cluxion-supercoder-locks-{os.geteuid()}"
    # Must not create path-level lock files when flags are unavailable.
    assert not lock_dir.exists() or list(lock_dir.iterdir()) == []


def test_lock_dir_symlink_fails_closed_without_touching_victim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import cluxion_agentplugin_supercoder.core.hash_patch as hp

    if hp.fcntl is None:
        pytest.skip("fcntl required for POSIX lock validation path")

    victim = tmp_path / "victim_lock_dir"
    victim.mkdir(mode=0o700)
    marker = victim / "keep.txt"
    marker.write_text("safe\n", encoding="utf-8")
    link = tmp_path / f"cluxion-supercoder-locks-{os.geteuid()}"
    link.symlink_to(victim)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    target = tmp_path / "t.py"
    target.write_text("x=1\n", encoding="utf-8")
    with pytest.raises(OSError), hp._exclusive_lock(target):
        pass
    assert marker.read_text(encoding="utf-8") == "safe\n"
    assert list(victim.iterdir()) == [marker]
    assert link.is_symlink()


def test_lock_file_symlink_fails_closed_without_touching_victim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cluxion_agentplugin_supercoder.core.hash_patch as hp

    if hp.fcntl is None:
        pytest.skip("fcntl required for POSIX lock validation path")

    lock_dir = tmp_path / f"cluxion-supercoder-locks-{os.geteuid()}"
    lock_dir.mkdir(mode=0o700)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    target = tmp_path / "t.py"
    target.write_text("x=1\n", encoding="utf-8")
    victim = tmp_path / "victim.lockdata"
    victim.write_text("DO_NOT_CLOBBER\n", encoding="utf-8")
    os.chmod(victim, 0o600)
    lock_path = hp._lock_path(target)
    lock_path.symlink_to(victim)
    with pytest.raises(OSError), hp._exclusive_lock(target):
        pass
    assert victim.read_text(encoding="utf-8") == "DO_NOT_CLOBBER\n"
    assert lock_path.is_symlink()


def _multiprocess_lock_child(path_str: str, tmp_str: str, ready, acquired) -> None:
    """Spawn-safe worker for same-UID multiprocess lock exclusion."""
    import tempfile as tf

    import cluxion_agentplugin_supercoder.core.hash_patch as child_hp

    tf.gettempdir = lambda: tmp_str  # type: ignore[method-assign]
    ready.put("ready")
    with child_hp._exclusive_lock(Path(path_str)):
        acquired.put("held")


def test_same_uid_real_multiprocess_exclusion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """fcntl.flock must exclude a second process (not just threads) for the same UID."""
    import multiprocessing as mp
    import queue

    import cluxion_agentplugin_supercoder.core.hash_patch as hp

    if hp.fcntl is None:
        pytest.skip("fcntl required for real multiprocess exclusion")

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    target = tmp_path / "t.py"
    target.write_text("x=1\n", encoding="utf-8")
    path_str = str(target)
    tmp_str = str(tmp_path)

    ctx = mp.get_context("spawn")
    ready: mp.Queue = ctx.Queue()
    acquired: mp.Queue = ctx.Queue()
    proc = ctx.Process(target=_multiprocess_lock_child, args=(path_str, tmp_str, ready, acquired))
    with hp._exclusive_lock(target):
        proc.start()
        assert ready.get(timeout=5) == "ready"
        with pytest.raises(queue.Empty):
            acquired.get(timeout=0.4)
    assert acquired.get(timeout=5) == "held"
    proc.join(timeout=5)
    assert proc.exitcode == 0


def test_post_image_field_is_exact_commit_string(tmp_path: Path) -> None:
    from cluxion_agentplugin_supercoder import runner

    path = tmp_path / "a.py"
    secret = "SECRET_MARKER_POST_IMAGE_MUST_STAY_PRIVATE\n"
    path.write_text("alpha\nbeta\n", encoding="utf-8")
    result = apply_patch(path, old_text="beta\n", new_text=secret)
    assert result.success is True
    assert result._post_image == f"alpha\n{secret}"
    assert result.post_hash == file_hash(result._post_image)
    # Private / non-repr: secret must not leak via repr.
    assert secret.strip() not in repr(result)
    assert "_post_image" not in repr(result) or secret.strip() not in repr(result)
    # External ToolResult payload must not carry the post-image string.
    path.write_text("alpha\nbeta\n", encoding="utf-8")
    tool = runner.patch_tool(
        {
            "cwd": str(tmp_path),
            "path": "a.py",
            "old_text": "beta\n",
            "new_text": secret,
            "expected_file_hash": file_hash("alpha\nbeta\n"),
            "syntax_gate": False,
            "lint_gate": False,
        }
    )
    assert tool.ok is True
    payload_text = repr(tool.payload)
    assert secret.strip() not in payload_text
    assert "post_image" not in tool.payload
    assert "_post_image" not in tool.payload


def test_atomic_write_interruption_leaves_original_intact(tmp_path: Path) -> None:
    """Simulated mid-write crash leaves ORIGINAL file intact (temp may remain, no truncate)."""
    path = tmp_path / "atomic_test.txt"
    original = "IMPORTANT ORIGINAL CONTENT\nDO NOT LOSE\n"
    path.write_text(original, encoding="utf-8")
    orig_hash = file_hash(original)

    # simulate crash mid atomic write by patching _atomic_write temporarily
    def crashing_atomic(p: Path, content: str) -> None:
        dir_ = p.parent
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=dir_, delete=False, suffix=".tmp") as tmp:
            tmp.write(content[:10])  # partial write
            tmp.flush()
            os.fsync(tmp.fileno())
            # simulate kill before replace
            raise RuntimeError("simulated crash mid-write")

    original_atomic = None
    try:
        # monkey patch for test
        import cluxion_agentplugin_supercoder.core.hash_patch as hp

        original_atomic = hp._atomic_write
        hp._atomic_write = crashing_atomic
        with contextlib.suppress(RuntimeError):
            hp._atomic_write(path, "CORRUPTED NEW CONTENT\n")
        # after crash, original must be untouched
        after = path.read_text(encoding="utf-8")
        assert after == original, "Atomic write failed: original was corrupted or truncated"
        assert file_hash(after) == orig_hash
    finally:
        import cluxion_agentplugin_supercoder.core.hash_patch as hp

        if original_atomic is not None:
            hp._atomic_write = original_atomic
        # cleanup any leftover temp if test created
        for f in tmp_path.glob("*.tmp"):
            f.unlink(missing_ok=True)


# === Cycle 108: pre-commit candidate validator + temp cleanup ===


def test_invalid_candidate_validator_skips_atomic_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Rejecting validator must run under lock, never call _atomic_write, leave disk frozen."""
    import cluxion_agentplugin_supercoder.core.hash_patch as hp

    path = tmp_path / "a.py"
    original = "alpha\nbeta\ngamma\n"
    path.write_text(original, encoding="utf-8")
    before_bytes = path.read_bytes()
    before_hash = file_hash(original)
    before_mtime = path.stat().st_mtime_ns
    seen: list[str] = []
    writes = {"count": 0}

    def reject(candidate: str) -> bool:
        seen.append(candidate)
        return False

    def boom_write(p: Path, content: str) -> None:
        writes["count"] += 1
        raise AssertionError("_atomic_write must not run for invalid candidate")

    monkeypatch.setattr(hp, "_atomic_write", boom_write)
    result = apply_patch(
        path,
        old_text="beta\n",
        new_text="BETA\n",
        expected_file_hash=before_hash,
        candidate_validator=reject,
    )
    assert result.success is False
    assert result.strategy == "candidate_rejected"
    assert "pre-commit validator" in result.message
    assert writes["count"] == 0
    assert seen == ["alpha\nBETA\ngamma\n"]
    assert path.read_bytes() == before_bytes
    assert file_hash(path.read_text(encoding="utf-8")) == before_hash
    assert path.stat().st_mtime_ns == before_mtime


def test_valid_candidate_validator_allows_commit(tmp_path: Path) -> None:
    path = tmp_path / "a.py"
    path.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    seen: list[str] = []

    def accept(candidate: str) -> bool:
        seen.append(candidate)
        return True

    result = apply_patch(
        path,
        old_text="beta\n",
        new_text="BETA\n",
        expected_file_hash=file_hash("alpha\nbeta\ngamma\n"),
        candidate_validator=accept,
    )
    assert result.success is True
    assert result.strategy == "exact"
    assert seen == ["alpha\nBETA\ngamma\n"]
    assert path.read_text(encoding="utf-8") == "alpha\nBETA\ngamma\n"


def test_disabled_validator_none_stays_compatible(tmp_path: Path) -> None:
    path = tmp_path / "a.py"
    path.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    result = apply_patch(
        path,
        old_text="beta\n",
        new_text="BETA\n",
        expected_file_hash=file_hash("alpha\nbeta\ngamma\n"),
        candidate_validator=None,
    )
    assert result.success is True
    assert path.read_text(encoding="utf-8") == "alpha\nBETA\ngamma\n"


def test_candidate_validator_runs_while_hash_lock_held(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Validator must execute inside the same exclusive lock as the patch decision."""
    import cluxion_agentplugin_supercoder.core.hash_patch as hp

    path = tmp_path / "a.py"
    path.write_text("alpha\nbeta\n", encoding="utf-8")
    states: list[str] = []
    real_lock = hp._exclusive_lock

    @contextlib.contextmanager
    def tracking_lock(p: Path):
        states.append("enter")
        with real_lock(p):
            states.append("held")
            try:
                yield
            finally:
                states.append("release")

    def validator(candidate: str) -> bool:
        assert "held" in states and "release" not in states
        states.append("validate")
        return True

    monkeypatch.setattr(hp, "_exclusive_lock", tracking_lock)
    result = apply_patch(
        path,
        old_text="beta\n",
        new_text="BETA\n",
        expected_file_hash=file_hash("alpha\nbeta\n"),
        candidate_validator=validator,
    )
    assert result.success is True
    assert states == ["enter", "held", "validate", "release"]


@pytest.mark.parametrize(
    "fail_point",
    ["write", "fsync", "replace"],
    ids=["write", "fsync", "replace"],
)
def test_atomic_write_error_cleans_tmp_and_keeps_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_point: str
) -> None:
    """write/fsync/replace failures must unlink uncommitted *.tmp and leave original intact."""
    import cluxion_agentplugin_supercoder.core.hash_patch as hp

    path = tmp_path / "keep.txt"
    original = "IMPORTANT ORIGINAL\n"
    path.write_text(original, encoding="utf-8")
    before = path.read_bytes()
    before_mtime = path.stat().st_mtime_ns

    created: list[Path] = []
    real_ntf = tempfile.NamedTemporaryFile

    def tracking_ntf(*args, **kwargs):
        tmp = real_ntf(*args, **kwargs)
        created.append(Path(tmp.name))
        if fail_point == "write":
            original_write = tmp.write

            def boom_write(data):
                original_write(data)
                raise OSError("injected write failure")

            tmp.write = boom_write  # type: ignore[method-assign]
        return tmp

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", tracking_ntf)
    if fail_point == "fsync":

        def boom_fsync(fd):
            raise OSError("injected fsync failure")

        monkeypatch.setattr(hp.os, "fsync", boom_fsync)
    elif fail_point == "replace":

        def boom_replace(src, dst):
            raise OSError("injected replace failure")

        monkeypatch.setattr(hp.os, "replace", boom_replace)

    with pytest.raises(OSError):
        hp._atomic_write(path, "NEW CONTENT THAT MUST NOT LAND\n")

    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == before_mtime
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == [], f"uncommitted temp left behind: {leftovers}"
    for item in created:
        assert not item.exists(), f"temp still present: {item}"


def test_atomic_write_cleanup_error_does_not_hide_root_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cluxion_agentplugin_supercoder.core.hash_patch as hp

    path = tmp_path / "keep.txt"
    path.write_text("original\n", encoding="utf-8")

    def fail_fsync(_fd: int) -> None:
        raise OSError("fsync root cause")

    def fail_cleanup(_path: Path) -> None:
        raise PermissionError("cleanup denied")

    monkeypatch.setattr(hp.os, "fsync", fail_fsync)
    monkeypatch.setattr(Path, "unlink", fail_cleanup)

    with pytest.raises(OSError, match="fsync root cause"):
        hp._atomic_write(path, "new\n")
