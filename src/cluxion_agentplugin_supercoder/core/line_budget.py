"""Line budget policy — blocks oversized reads and writes."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    reason: str
    max_lines: int
    remaining_lines: int


_DEFAULTS = {
    "inspect": 120,
    "patch_context": 100,
    "review": 160,
    "refactor_unit": 250,
    "create_file": 400,
    "test_log": 120,
}


def budget_for(mode: str, *, requested_lines: int, remaining: int = 10_000) -> BudgetDecision:
    cap = _DEFAULTS.get(mode, 120)
    if requested_lines > cap:
        return BudgetDecision(False, f"line_budget_exceeded:{mode}", cap, remaining)
    if requested_lines > remaining:
        return BudgetDecision(False, "session_line_budget_exhausted", cap, remaining)
    return BudgetDecision(True, "within_budget", cap, remaining - requested_lines)


_ASCII_CODING_KEYWORDS = (
    "code",
    "fix",
    "implement",
    "refactor",
    "patch",
    "test",
    "bug",
    "debug",
)
_KOREAN_CODING_KEYWORDS = (
    "코드",
    "수정",
    "구현",
    "리팩터",
    "패치",
    "테스트",
    "버그",
)
_ASCII_CODING_PATTERNS = tuple(
    re.compile(rf"\b{re.escape(keyword)}\b", re.IGNORECASE) for keyword in _ASCII_CODING_KEYWORDS
)


def is_coding_task(prompt: str) -> bool:
    text = prompt.lower()
    if any(pattern.search(text) for pattern in _ASCII_CODING_PATTERNS):
        return True
    return any(keyword in text for keyword in _KOREAN_CODING_KEYWORDS)


__all__ = ["BudgetDecision", "budget_for", "is_coding_task"]
