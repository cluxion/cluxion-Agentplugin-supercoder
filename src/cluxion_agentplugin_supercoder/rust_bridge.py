"""Repo index bridge with a three-tier backend chain.

Backend resolution order (override with CLUXION_SUPERCODER_BACKEND):
1. ``native``     — in-process Rust extension (supercoder_index_native)
2. ``subprocess`` — Rust CLI binary over JSON stdin/stdout
3. ``python``     — pure-Python walk + hash, semantics-identical
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

INDEX_BIN_ENV = "CLUXION_SUPERCODER_INDEX_BIN"
INDEX_BACKEND_ENV = "CLUXION_SUPERCODER_BACKEND"

DEFAULT_MAX_FILES = 256
SKIP_DIRS = {".git", "node_modules", ".venv", "dist", "target"}
DEFAULT_EXTENSIONS = (".py", ".rs", ".ts", ".tsx", ".js", ".go", ".md", ".toml", ".yaml", ".yml")

try:
    import supercoder_index_native as _native
except ImportError:
    _native = None


def resolve_backend() -> str:
    """Pick the best available backend, honoring the env override."""
    forced = os.environ.get(INDEX_BACKEND_ENV, "").strip().lower()
    if forced in ("native", "subprocess", "python"):
        return forced
    if _native is not None:
        return "native"
    if shutil.which(_binary()) is not None:
        return "subprocess"
    return "python"


def index_available() -> bool:
    """Return True when any index backend is usable (always true: python fallback)."""
    return True


def scan_repo(
    root: Path,
    *,
    max_files: int = DEFAULT_MAX_FILES,
    extensions: tuple[str, ...] = DEFAULT_EXTENSIONS,
) -> list[dict[str, object]]:
    """Index files under root: sorted, capped, each entry {path, file_hash, total_lines}."""
    payload = {
        "root": str(root),
        "max_files": max_files,
        "extensions": list(extensions),
    }
    backend = resolve_backend()
    if backend == "python":
        result = _py_scan(root, max_files=max_files, extensions=extensions)
    else:
        try:
            result = _invoke_native("scan", payload) if backend == "native" else _invoke_subprocess("scan", payload)
        except Exception:
            result = _py_scan(root, max_files=max_files, extensions=extensions)
    entries = result.get("entries")
    return entries if isinstance(entries, list) else []


def _invoke_native(command: str, payload: dict[str, object]) -> dict[str, object]:
    if _native is None:
        raise RuntimeError("native backend forced but supercoder_index_native is not importable")
    raw = _native.run(command, json.dumps(payload, ensure_ascii=False))
    return _parse_backend_json(raw, command)


def _invoke_subprocess(command: str, payload: dict[str, object]) -> dict[str, object]:
    binary = _binary()
    if shutil.which(binary) is None:
        raise RuntimeError("supercoder-index binary not found")
    completed = subprocess.run(
        [binary, command],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        stdout = completed.stdout.strip()
        try:
            error = json.loads(stdout).get("error", "")
        except (json.JSONDecodeError, AttributeError):
            error = ""
        raise RuntimeError(error or completed.stderr.strip() or f"supercoder-index {command} failed")
    parsed = _parse_backend_json(completed.stdout, command)
    return parsed


def _parse_backend_json(raw: str, command: str) -> dict[str, object]:
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError(f"supercoder-index {command} returned invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"supercoder-index {command} returned non-object JSON")
    return parsed


def _collect_candidates(root: Path, *, extensions: tuple[str, ...]) -> list[str]:
    candidates: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix not in extensions:
            continue
        candidates.append(str(path.relative_to(root)))
    candidates.sort()
    return candidates


def count_scan_candidates(
    root: Path,
    *,
    extensions: tuple[str, ...] = DEFAULT_EXTENSIONS,
) -> int:
    """Return how many files match scan_repo criteria before the max_files cap."""
    return len(_collect_candidates(root, extensions=extensions))


def _py_scan(
    root: Path,
    *,
    max_files: int,
    extensions: tuple[str, ...],
) -> dict[str, object]:
    from cluxion_agentplugin_supercoder.core.hash_patch import file_hash

    candidates = _collect_candidates(root, extensions=extensions)
    entries: list[dict[str, object]] = []
    for rel in candidates[:max_files]:
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        entries.append(
            {
                "path": rel,
                "file_hash": file_hash(text),
                "total_lines": text.count("\n") + (1 if text else 0),
            }
        )
    return {
        "ok": True,
        "entries": entries,
        "count": len(entries),
        "total_candidates": len(candidates),
    }


def _binary() -> str:
    configured = os.environ.get(INDEX_BIN_ENV, "").strip()
    if configured:
        return configured
    local = (
        Path(__file__).resolve().parents[2] / "rust" / "supercoder_index" / "target" / "release" / "supercoder-index"
    )
    if local.exists():
        return str(local)
    return "supercoder-index"


__all__ = [
    "DEFAULT_EXTENSIONS",
    "DEFAULT_MAX_FILES",
    "INDEX_BACKEND_ENV",
    "INDEX_BIN_ENV",
    "count_scan_candidates",
    "index_available",
    "resolve_backend",
    "scan_repo",
]
