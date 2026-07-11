"""Fail-closed safety gates for tool calls."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SafetyDecision:
    decision: str
    reason: str


_DESTRUCTIVE = (
    "rm -rf",
    "git reset --hard",
    "git push --force",
    "drop table",
    "truncate table",
    "kubectl delete",
)
_SECRET_PARTS = (".env", "id_rsa", "credentials", "secrets")


def pre_tool_gate(
    tool_name: str,
    args: dict[str, object],
    *,
    workspace: Path,
    stale_cursor: bool = False,
) -> SafetyDecision:
    if stale_cursor:
        return SafetyDecision("block", "stale cursor: file changed after read_window")
    command = str(args.get("command", ""))
    if any(token in command.lower() for token in _DESTRUCTIVE):
        return SafetyDecision("block", "destructive command requires explicit approval")
    for key in ("path", "file_path", "target"):
        value = args.get(key)
        if isinstance(value, str) and value:
            decision = _path_gate(workspace, value)
            if decision.decision == "block":
                return decision
    if tool_name in {"write_file", "patch"} and int(args.get("line_count", 0) or 0) > 400:
        return SafetyDecision("block", "write exceeds 400-line soft cap")
    return SafetyDecision("allow", "passed safety gate")


def _path_gate(workspace: Path, rel_or_abs: str) -> SafetyDecision:
    candidate = Path(rel_or_abs)
    target = candidate if candidate.is_absolute() else workspace / candidate
    try:
        workspace_resolved = workspace.resolve()
        # Symlink final components use strict resolve so cyclic links raise
        # OSError/RuntimeError and fail closed; missing plain paths stay non-strict.
        resolved = target.resolve(strict=True) if target.is_symlink() else target.resolve()
    except (OSError, RuntimeError):
        return SafetyDecision("block", "path resolution failed")
    # String-prefix comparison would let sibling dirs through (/work vs /work2),
    # so containment is checked path-component-wise.
    if not resolved.is_relative_to(workspace_resolved):
        return SafetyDecision("block", "workspace escape blocked")
    # Only workspace-relative components count — ancestor dirs like Secret_Project
    # must never trip the secret gate.
    relative = resolved.relative_to(workspace_resolved)
    if _is_secret_relative(relative):
        return SafetyDecision("block", "secret file access blocked")
    return SafetyDecision("allow", "path ok")


def _is_secret_relative(relative: Path) -> bool:
    """Block exact sensitive basenames and dotted extensions (casefolded).

    ``part == token`` or ``part.startswith(token + ".")`` after casefolding:
    blocks ``.env``, ``.env.local``, ``CREDENTIALS.json``, ``id_rsa.pub`` while
    allowing ``secretsmanager.py`` and ``credentials-guide.md``.
    """
    for part in relative.parts:
        folded = part.casefold()
        for token in _SECRET_PARTS:
            token_folded = token.casefold()
            if folded == token_folded or folded.startswith(token_folded + "."):
                return True
    return False


__all__ = ["SafetyDecision", "pre_tool_gate"]
