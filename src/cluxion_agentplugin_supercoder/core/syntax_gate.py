"""L1 syntax gate: parse-check patched files before they land.

Backend chain mirrors rust_bridge (native tree-sitter -> subprocess ->
pure Python). The gate is fail-open: a language nobody can parse reports
``checked: False`` and never blocks a patch. The pure-Python tier covers
python/json/toml via the stdlib; tree-sitter tiers add rust/js/ts/tsx.
TOML is always checked in Python (tomllib) so every tier agrees on it.
"""

from __future__ import annotations

import ast
import json
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
    if resolved == "toml":
        return _py_check(content, "toml")
    backend = rust_bridge.resolve_backend()
    payload: dict[str, object] = {"content": content, "language": resolved}
    if backend == "native":
        return rust_bridge._invoke_native("syntax-check", payload)
    if backend == "subprocess":
        return rust_bridge._invoke_subprocess("syntax-check", payload)
    return _py_check(content, resolved)


def _py_check(content: str, language: str) -> dict[str, Any]:
    if language not in PYTHON_TIER_LANGUAGES:
        return _unchecked(language)
    errors: list[dict[str, Any]] = []
    if language == "python":
        try:
            ast.parse(content)
        except SyntaxError as exc:
            errors.append(_finding(exc.lineno or 1, exc.offset or 1, exc.msg or "syntax error", content))
    elif language == "json":
        try:
            json.loads(content)
        except json.JSONDecodeError as exc:
            errors.append(_finding(exc.lineno, exc.colno, exc.msg, content))
    else:
        try:
            tomllib.loads(content)
        except tomllib.TOMLDecodeError as exc:
            errors.append(_finding(1, 1, str(exc), content))
    return {
        "ok": True,
        "checked": True,
        "language": language,
        "valid": not errors,
        "errors": errors[:MAX_REPORTED_ERRORS],
        "error_count": len(errors[:MAX_REPORTED_ERRORS]),
    }


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
    lines = content.splitlines()
    snippet = lines[line - 1][:120] if 0 < line <= len(lines) else ""
    return {"line": line, "column": column, "kind": "error", "message": message, "snippet": snippet}


__all__ = ["LANGUAGE_BY_EXTENSION", "check_source", "language_for_path"]
