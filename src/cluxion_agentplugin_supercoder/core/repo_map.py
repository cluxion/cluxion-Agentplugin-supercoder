"""L0 repo map: compact repo structure plus symbol outline for small models.

Backend chain mirrors rust_bridge (native tree-sitter -> subprocess ->
pure Python). Per-file fail-open: a language no backend can outline still
contributes its path and line count. The map is budgeted, never silently
truncated — files that do not fit are counted in ``files_omitted``.
The pure-Python tier outlines Python via the stdlib ast module only;
tree-sitter tiers add rust/js/ts/tsx.
"""

from __future__ import annotations

import ast
from collections import OrderedDict
from pathlib import Path
from typing import Any

from cluxion_agentplugin_supercoder import rust_bridge
from cluxion_agentplugin_supercoder.core.syntax_gate import language_for_path

DEFAULT_MAX_FILES = 128
DEFAULT_MAX_SYMBOLS_PER_FILE = 24
DEFAULT_BUDGET_CHARS = 8_000
OUTLINE_LANGUAGES = {"python", "rust", "javascript", "typescript", "tsx"}
SIGNATURE_MAX_CHARS = 120
_OUTLINE_CACHE_MAX = 4096
_outline_cache: OrderedDict[tuple[str, str], list[dict[str, Any]]] = OrderedDict()


def clear_outline_cache() -> None:
    """Drop all cached per-file symbol outlines."""
    _outline_cache.clear()


def build_repo_map(
    root: Path | str,
    *,
    max_files: int = DEFAULT_MAX_FILES,
    max_symbols_per_file: int = DEFAULT_MAX_SYMBOLS_PER_FILE,
    budget_chars: int = DEFAULT_BUDGET_CHARS,
) -> dict[str, Any]:
    """Build a budgeted text map of the repo with per-file symbol outlines."""
    base = Path(root).resolve()
    if not base.is_dir():
        return {"ok": False, "error": f"root is not a directory: {base}"}
    entries = rust_bridge.scan_repo(base, max_files=max(1, int(max_files)))
    entries = _rank_entries(entries)

    lines: list[str] = []
    used = 0
    files_mapped = 0
    files_omitted = 0
    outlined_files = 0
    symbol_total = 0
    budget = max(200, int(budget_chars))
    per_file_cap = max(1, int(max_symbols_per_file))
    for entry in entries:
        rel = str(entry.get("path", ""))
        total_lines = int(entry.get("total_lines", 0))
        if files_omitted:  # budget already exhausted: only count the rest
            files_omitted += 1
            continue
        block = [f"{rel} ({total_lines}L)"]
        language = language_for_path(rel) or ""
        if language in OUTLINE_LANGUAGES:
            symbols, _ = _outline_for_map_entry(
                base / rel,
                language=language,
                file_hash=str(entry.get("file_hash") or ""),
            )
            if symbols:
                outlined_files += 1
            shown = symbols[:per_file_cap]
            symbol_total += len(shown)
            for symbol in shown:
                indent = "    " if int(symbol.get("depth", 0)) else "  "
                block.append(f"{indent}{symbol['kind']} {symbol['name']}:{symbol['line']}")
            if len(symbols) > per_file_cap:
                block.append(f"  ... +{len(symbols) - per_file_cap} more symbols")
        block_text = "\n".join(block)
        if used + len(block_text) + 1 > budget:
            files_omitted += 1
            continue
        lines.append(block_text)
        used += len(block_text) + 1
        files_mapped += 1

    return {
        "ok": True,
        "root": str(base),
        "backend": rust_bridge.resolve_backend(),
        "map": "\n".join(lines),
        "files_scanned": len(entries),
        "files_mapped": files_mapped,
        "files_omitted": files_omitted,
        "outlined_files": outlined_files,
        "symbol_count": symbol_total,
        "truncated": files_omitted > 0,
    }


def _outline_for_map_entry(
    path: Path,
    *,
    language: str,
    file_hash: str,
) -> tuple[list[dict[str, Any]], bool]:
    """Resolve symbols for build_repo_map, using the content-hash cache when possible."""
    if not file_hash:
        return outline_file(path, language=language), False
    key = (str(path), file_hash)
    cached = _outline_cache.get(key)
    if cached is not None:
        _outline_cache.move_to_end(key)
        return cached, True
    symbols = outline_file(path, language=language)
    _outline_cache[key] = symbols
    while len(_outline_cache) > _OUTLINE_CACHE_MAX:
        _outline_cache.popitem(last=False)
    return symbols, False


def _rank_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Hidden paths (dot-directories like .github, .pytest_cache) are noise
    for orientation and are dropped; code files outrank docs/config so the
    budget is spent on what the model will actually edit. Within code,
    src/ leads, tests trail, everything else sits in between."""
    visible = [
        entry for entry in entries if not any(part.startswith(".") for part in str(entry.get("path", "")).split("/"))
    ]
    return sorted(visible, key=lambda entry: (_rank_group(str(entry.get("path", ""))), str(entry.get("path", ""))))


def _rank_group(rel: str) -> int:
    if (language_for_path(rel) or "") not in OUTLINE_LANGUAGES:
        return 3
    parts = rel.split("/")
    if parts[0] in ("tests", "test"):
        return 2
    if parts[0] in ("src", "lib", "app"):
        return 0
    return 1


def outline_file(path: Path, *, language: str | None = None) -> list[dict[str, Any]]:
    """Outline one file's top-level symbols via the backend chain (fail-open)."""
    resolved = language or language_for_path(path) or ""
    if resolved not in OUTLINE_LANGUAGES:
        return []
    backend = rust_bridge.resolve_backend()
    payload = {"path": str(path), "language": resolved}
    try:
        if backend == "native":
            result = rust_bridge._invoke_native("outline", payload)
        elif backend == "subprocess":
            result = rust_bridge._invoke_subprocess("outline", payload)
        else:
            result = _py_outline(path, resolved)
    except (RuntimeError, OSError):
        return []
    symbols = result.get("symbols")
    return symbols if isinstance(symbols, list) else []


def _py_outline(path: Path, language: str) -> dict[str, Any]:
    """Stdlib tier: ast outlines Python; other languages fail open."""
    if language != "python":
        return {"ok": True, "checked": False, "language": language, "reason": "no_outline", "symbols": []}
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, UnicodeDecodeError, SyntaxError, ValueError):
        return {"ok": True, "checked": False, "language": language, "reason": "unreadable", "symbols": []}
    source_lines = source.splitlines()
    symbols: list[dict[str, Any]] = []
    for node in tree.body:
        entry = _py_symbol(node, source_lines, depth=0)
        if entry is None:
            continue
        symbols.append(entry)
        if isinstance(node, ast.ClassDef):
            for member in node.body:
                member_entry = _py_symbol(member, source_lines, depth=1)
                if member_entry is not None:
                    symbols.append(member_entry)
    return {"ok": True, "checked": True, "language": language, "symbols": symbols}


def _py_symbol(node: ast.stmt, source_lines: list[str], *, depth: int) -> dict[str, Any] | None:
    if isinstance(node, ast.ClassDef):
        kind = "class"
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        kind = "method" if depth else "function"
    else:
        return None
    line = node.lineno
    signature = source_lines[line - 1].strip()[:SIGNATURE_MAX_CHARS] if line <= len(source_lines) else ""
    return {
        "kind": kind,
        "name": node.name,
        "line": line,
        "end_line": int(node.end_lineno or line),
        "depth": depth,
        "signature": signature,
    }


__all__ = [
    "DEFAULT_BUDGET_CHARS",
    "DEFAULT_MAX_FILES",
    "DEFAULT_MAX_SYMBOLS_PER_FILE",
    "OUTLINE_LANGUAGES",
    "build_repo_map",
    "clear_outline_cache",
    "outline_file",
]
