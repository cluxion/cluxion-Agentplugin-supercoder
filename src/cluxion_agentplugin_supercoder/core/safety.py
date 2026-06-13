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
    resolved = (workspace / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    # String-prefix comparison would let sibling dirs through (/work vs /work2),
    # so containment is checked path-component-wise.
    if not resolved.is_relative_to(workspace.resolve()):
        return SafetyDecision("block", "workspace escape blocked")
    if any(part in _SECRET_PARTS for part in resolved.parts):
        return SafetyDecision("block", "secret file access blocked")
    return SafetyDecision("allow", "path ok")


__all__ = ["SafetyDecision", "pre_tool_gate"]
