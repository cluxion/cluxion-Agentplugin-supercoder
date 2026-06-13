"""L4 retry guidance: track patch failures per file and tell the host
model what to do differently on the next attempt.

The plugin cannot author a new patch itself — the host model does — so
the correction loop is closed by feedback: every failure returns an
attempt counter, a failure-specific instruction, and an escalation flag
once retries stop making progress. State is in-process (the plugin host
is a long-lived process) and bounded by an LRU cap, cleared on success.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from dataclasses import dataclass

MAX_ATTEMPTS = 3
MAX_TRACKED_FILES = 256

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


_failures: OrderedDict[tuple[str, str], list[str]] = OrderedDict()


def record_failure(workspace: str, path: str, reason: str, *, old_text: str = "") -> RetryAdvice:
    """Record a patch failure and return guidance for the next attempt."""
    key = (workspace, path)
    signature = f"{reason}:{_digest(old_text)}"
    history = _failures.pop(key, [])
    repeated = bool(history) and history[-1] == signature
    history.append(signature)
    _failures[key] = history
    while len(_failures) > MAX_TRACKED_FILES:
        _failures.popitem(last=False)
    attempt = len(history)
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


def reset() -> None:
    """Drop all tracked state (test isolation)."""
    _failures.clear()


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


__all__ = ["MAX_ATTEMPTS", "RetryAdvice", "record_failure", "record_success", "reset"]
