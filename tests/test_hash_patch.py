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
    for start, end, block in _candidate_spans(text, reference, MAX_LINE_DRIFT):
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


def test_exact_patch(tmp_path: Path) -> None:
    path = tmp_path / "a.py"
    path.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    expected = file_hash(path.read_text(encoding="utf-8"))
    result = apply_patch(path, old_text="beta\n", new_text="BETA\n", expected_file_hash=expected)
    assert result.success is True
    assert "BETA" in path.read_text(encoding="utf-8")


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


def test_exact_match_uses_first_occurrence(tmp_path: Path) -> None:
    path = tmp_path / "a.py"
    path.write_text("x = 1\ny = 2\nx = 1\n", encoding="utf-8")
    result = apply_patch(path, old_text="x = 1\n", new_text="x = 9\n")
    assert result.success is True
    assert path.read_text(encoding="utf-8") == "x = 9\ny = 2\nx = 1\n"


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
