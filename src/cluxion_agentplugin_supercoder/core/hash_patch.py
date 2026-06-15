"""Hash-verified safe patch — ported from cluxion-os _hash_edit_core."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

DEFAULT_FUZZY_THRESHOLD = 0.86
MAX_CONTEXT_SCAN = 8
MAX_LINE_DRIFT = 2


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
    if not path.exists():
        return _failed(str(path), "missing_file", expected_file_hash, "file not found")
    text = path.read_text(encoding="utf-8")
    current_hash = file_hash(text)
    if expected_file_hash and current_hash != _normalize_hash(expected_file_hash):
        return _failed(str(path), "stale_file", expected_file_hash, "file changed since cursor was created")
    exact = _exact_spans(text, old_text)
    if exact:
        start, end = exact[0]
        return _commit(path, text, start, end, new_text, "exact", expected_file_hash or current_hash, current_hash, 1.0)
    fuzzy = _best_fuzzy_span(text, old_text)
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
        )
    return _failed(str(path), "no_match", expected_file_hash or current_hash, "patch target not found")


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
    spans: list[tuple[int, int]] = []
    offset = 0
    while True:
        start = text.find(needle, offset)
        if start < 0:
            return spans
        spans.append((start, start + len(needle)))
        offset = start + len(needle)


def _candidate_spans(text: str, reference: str, line_drift: int) -> list[tuple[int, int, str]]:
    lines = text.splitlines(keepends=True)
    if not lines:
        return []
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    target = max(1, len(reference.splitlines(keepends=True)))
    lower = max(1, target - line_drift)
    upper = min(len(lines), target + line_drift)
    spans: list[tuple[int, int, str]] = []
    for width in range(lower, upper + 1):
        for start_line in range(0, len(lines) - width + 1):
            start = offsets[start_line]
            end = offsets[start_line + width]
            block = text[start:end]
            spans.append((start, end, block))
    return spans


def _best_fuzzy_span(text: str, reference: str) -> tuple[int, int, str, float, bool] | None:
    best: tuple[int, int, str, float] | None = None
    best_lines: tuple[int, int] | None = None
    ambiguous = False
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    for start, end, block in _candidate_spans(text, reference, MAX_LINE_DRIFT):
        # compute line range [start_line, end_line) for overlap test
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
        elif score >= DEFAULT_FUZZY_THRESHOLD and best and abs(score - best[3]) < 0.015:
            # only treat as ambiguous if a genuinely different (non-overlapping) location matches closely
            if best_lines is not None and not (end_line <= best_lines[0] or start_line >= best_lines[1]):
                continue  # overlapping window on same location -> not real ambiguity
            ambiguous = True
    if best is None:
        return None
    return best[0], best[1], best[2], best[3], ambiguous


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
) -> PatchResult:
    updated = f"{text[:start]}{new_content}{text[end:]}"
    path.write_text(updated, encoding="utf-8")
    return PatchResult(True, str(path), strategy, "patch applied", expected, matched, round(score, 4), 1)


def _failed(path: str, strategy: str, expected: str, message: str, score: float = 0.0) -> PatchResult:
    return PatchResult(False, path, strategy, message, expected, None, round(score, 4), 0)


__all__ = ["PatchResult", "apply_patch", "file_hash", "hash_block"]
