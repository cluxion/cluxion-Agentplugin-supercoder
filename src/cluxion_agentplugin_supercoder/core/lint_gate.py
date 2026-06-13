"""L2 lint gate: advisory lint findings for the file a patch just changed.

The engine is ruff — a Rust linter shipped as a wheel dependency of this
plugin, so the gate works everywhere without asking the host project to
install anything. It runs on a single file, respects the target project's
own ruff configuration (config discovery walks up from the file), and is
suggest-only: findings ride along on the patch result and never block or
revert a patch. Languages without a wired linter report ``checked: False``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from cluxion_agentplugin_supercoder.core.syntax_gate import language_for_path

RUFF_BIN_ENV = "CLUXION_SUPERCODER_RUFF_BIN"
LINTABLE_LANGUAGES = {"python"}
MAX_REPORTED_FINDINGS = 20
_TIMEOUT_SECONDS = 15.0


def ruff_bin() -> str | None:
    """Resolve the ruff executable: env override, venv sibling, then PATH."""
    override = os.environ.get(RUFF_BIN_ENV, "").strip()
    if override:
        return override if Path(override).exists() else None
    return _discover_ruff()


@lru_cache(maxsize=1)
def _discover_ruff() -> str | None:
    name = "ruff.exe" if os.name == "nt" else "ruff"
    sibling = Path(sys.executable).with_name(name)
    if sibling.exists():
        return str(sibling)
    return shutil.which("ruff")


def check_file(path: str | Path, *, cwd: str | Path | None = None) -> dict[str, Any]:
    """Lint one file and return structured advisory findings."""
    target = Path(path)
    language = language_for_path(target)
    if language not in LINTABLE_LANGUAGES:
        return _unchecked(language or "", "no_linter")
    binary = ruff_bin()
    if binary is None:
        return _unchecked(language, "no_tool")
    command = [binary, "check", "--output-format", "json", "--force-exclude", "--no-cache", str(target)]
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _unchecked(language, f"tool_error:{type(exc).__name__}")
    # ruff exits 0 (clean) or 1 (findings); anything else is a tool failure.
    if proc.returncode not in (0, 1):
        return _unchecked(language, "tool_error:exit")
    try:
        raw = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return _unchecked(language, "tool_error:output")
    findings = [
        {
            "line": int(item.get("location", {}).get("row", 1)),
            "column": int(item.get("location", {}).get("column", 1)),
            "code": item.get("code") or "",
            "message": str(item.get("message", "")),
            "fixable": item.get("fix") is not None,
        }
        for item in raw
        if isinstance(item, dict)
    ]
    total = len(findings)
    return {
        "ok": True,
        "checked": True,
        "language": language,
        "tool": "ruff",
        "clean": total == 0,
        "findings": findings[:MAX_REPORTED_FINDINGS],
        "finding_count": total,
        "truncated": total > MAX_REPORTED_FINDINGS,
    }


def _unchecked(language: str, reason: str) -> dict[str, Any]:
    return {
        "ok": True,
        "checked": False,
        "language": language,
        "reason": reason,
        "clean": True,
        "findings": [],
        "finding_count": 0,
        "truncated": False,
    }


__all__ = ["LINTABLE_LANGUAGES", "MAX_REPORTED_FINDINGS", "RUFF_BIN_ENV", "check_file", "ruff_bin"]
