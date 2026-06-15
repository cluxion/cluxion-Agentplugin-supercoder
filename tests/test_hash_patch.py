from __future__ import annotations

from pathlib import Path

import pytest

from cluxion_agentplugin_supercoder.core.hash_patch import apply_patch, file_hash


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
