"""L1 syntax gate: parse-check patched files before they land.

Public python/json/toml truth is always the stdlib tier (backend-independent).
Native/subprocess tree-sitter remains for rust/js/ts/tsx. The gate is fail-open:
a language nobody can parse reports ``checked: False`` and never blocks a patch.
"""

from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path
from typing import Any

from cluxion_agentplugin_supercoder import rust_bridge

LANGUAGE_BY_EXTENSION = {
    ".py": "python",
    ".rs": "rust",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    ".json": "json",
    ".toml": "toml",
}
PYTHON_TIER_LANGUAGES = {"python", "json", "toml"}
MAX_REPORTED_ERRORS = 20


def language_for_path(path: str | Path) -> str | None:
    return LANGUAGE_BY_EXTENSION.get(Path(path).suffix.lower())


def check_source(
    *,
    path: str | Path | None = None,
    content: str | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    """Parse-check content (or a file) and return structured findings."""
    if content is None:
        if path is None:
            raise ValueError("content or path is required")
        content = Path(path).read_text(encoding="utf-8")
    resolved = language or (language_for_path(path) if path is not None else None) or ""
    # python/json/toml public truth is always stdlib — never native/subprocess.
    if resolved in PYTHON_TIER_LANGUAGES:
        return _py_check(content, resolved)
    backend = rust_bridge.resolve_backend()
    payload: dict[str, object] = {"content": content, "language": resolved}
    try:
        if backend == "native":
            result = rust_bridge._invoke_native("syntax-check", payload)
        elif backend == "subprocess":
            result = rust_bridge._invoke_subprocess("syntax-check", payload)
        else:
            return _py_check(content, resolved)
        if _valid_syntax_backend_result(result, resolved):
            return result
    except (RuntimeError, OSError, subprocess.SubprocessError, TypeError, KeyError):
        pass
    return _py_check(content, resolved)


def _valid_syntax_backend_result(result: object, language: str) -> bool:
    """Accept only a well-typed syntax-check dict; otherwise force Python fallback.

    Validation lives solely in check_source — no shared schema framework.
    """
    if not isinstance(result, dict):
        return False
    if result.get("ok") is not True:
        return False
    checked = result.get("checked")
    valid = result.get("valid")
    if checked is not True and checked is not False:
        return False
    if valid is not True and valid is not False:
        return False
    if result.get("language") != language:
        return False
    errors = result.get("errors")
    if not isinstance(errors, list):
        return False
    error_count = result.get("error_count")
    if isinstance(error_count, bool) or not isinstance(error_count, int):
        return False
    return error_count == len(errors)


def _py_check(content: str, language: str) -> dict[str, Any]:
    if language not in PYTHON_TIER_LANGUAGES:
        return _unchecked(language)
    errors: list[dict[str, Any]] = []
    if language == "python":
        try:
            # compile() rejects module-level return and duplicate args; ast.parse does not.
            compile(content, "<syntax-gate>", "exec", dont_inherit=True)
        except SyntaxError as exc:
            errors.append(_finding(exc.lineno or 1, exc.offset or 1, exc.msg or "syntax error", content))
    elif language == "json":
        errors.extend(_json_check(content))
    else:
        try:
            tomllib.loads(content)
        except tomllib.TOMLDecodeError as exc:
            errors.append(
                _finding(getattr(exc, "lineno", None) or 1, getattr(exc, "colno", None) or 1, str(exc), content)
            )
    return {
        "ok": True,
        "checked": True,
        "language": language,
        "valid": not errors,
        "errors": errors[:MAX_REPORTED_ERRORS],
        "error_count": len(errors[:MAX_REPORTED_ERRORS]),
    }


def _json_check(content: str) -> list[dict[str, Any]]:
    """RFC-strict JSON: empty/multi-root rejected by loads; NaN/Infinity via parse_constant."""
    rejected: list[str] = []

    def parse_constant(name: str) -> object:
        rejected.append(name)
        raise ValueError(f"non-RFC JSON constant: {name}")

    try:
        json.loads(content, parse_constant=parse_constant)
    except json.JSONDecodeError as exc:
        return [_finding(exc.lineno, exc.colno, exc.msg, content)]
    except ValueError as exc:
        name = rejected[0] if rejected else ""
        pos = _find_unquoted_json_constant(content, name) if name else None
        if pos is not None:
            line = content.count("\n", 0, pos) + 1
            last_nl = content.rfind("\n", 0, pos)
            column = pos - last_nl
            return [_finding(line, column, str(exc), content)]
        return [_finding(1, 1, str(exc), content)]
    return []


def _find_unquoted_json_constant(content: str, name: str) -> int | None:
    """Index of the first unquoted JSON constant token; skip quoted/escaped text."""
    if not name:
        return None
    in_string = False
    escape = False
    i = 0
    n = len(content)
    name_len = len(name)
    while i < n:
        ch = content[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            i += 1
            continue
        if content.startswith(name, i):
            end = i + name_len
            before = content[i - 1] if i > 0 else ""
            after = content[end] if end < n else ""
            if (not before or not (before.isalnum() or before == "_")) and (
                not after or not (after.isalnum() or after == "_")
            ):
                return i
        i += 1
    return None


def _unchecked(language: str) -> dict[str, Any]:
    return {
        "ok": True,
        "checked": False,
        "language": language,
        "reason": "no_parser",
        "valid": True,
        "errors": [],
        "error_count": 0,
    }


def _finding(line: int, column: int, message: str, content: str) -> dict[str, Any]:
    # LF-only line semantics so U+2028/U+2029/NEL/form-feed stay inside snippets.
    lines = content.split("\n")
    snippet = lines[line - 1][:120] if 0 < line <= len(lines) else ""
    return {"line": line, "column": column, "kind": "error", "message": message, "snippet": snippet}


__all__ = ["LANGUAGE_BY_EXTENSION", "check_source", "language_for_path"]
