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
import sys
from pathlib import Path

INDEX_BIN_ENV = "CLUXION_SUPERCODER_INDEX_BIN"
INDEX_BACKEND_ENV = "CLUXION_SUPERCODER_BACKEND"

DEFAULT_MAX_FILES = 256
SKIP_DIRS = {".git", "node_modules", ".venv", "dist", "target"}
DEFAULT_EXTENSIONS = (".py", ".rs", ".ts", ".tsx", ".js", ".go", ".md", ".toml", ".yaml", ".yml")

_native: object | None = None
_native_resolved = False
_fallback_warned = False
_BACKENDS = {"native", "subprocess", "python"}


def _load_native() -> object | None:
    """Import the native extension on first use, not at module load.

    The wheel import costs 100-300ms; --help and pure-python commands
    should never pay it.
    """
    global _native, _native_resolved
    if not _native_resolved:
        try:
            import supercoder_index_native

            _native = supercoder_index_native
        except ImportError:
            _native = None
        _native_resolved = True
    return _native


def resolve_backend() -> str:
    """Pick the best available backend, honoring the env override."""
    forced = _forced_backend()
    if forced is not None:
        return forced
    if _load_native() is not None:
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
    result = scan_repo_result(root, max_files=max_files, extensions=extensions)
    entries = result.get("entries")
    return entries if isinstance(entries, list) else []


def scan_repo_result(
    root: Path,
    *,
    max_files: int = DEFAULT_MAX_FILES,
    extensions: tuple[str, ...] = DEFAULT_EXTENSIONS,
) -> dict[str, object]:
    """Index files and return backend metadata for structured tool output."""
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
        except Exception as exc:
            if _forced_backend() == backend:
                return {
                    "ok": False,
                    "error": "backend_unavailable",
                    "message": f"{backend} backend is forced but unavailable: {type(exc).__name__}: {exc}",
                    "hint": (
                        f"Install/fix the {backend} backend or unset {INDEX_BACKEND_ENV}={backend} to allow fallback."
                    ),
                }
            _warn_fallback(backend, exc)
            result = _py_scan(root, max_files=max_files, extensions=extensions)
            result["fallback_from"] = backend
    result["backend"] = "python" if result.get("fallback_from") else backend
    result.setdefault("ok", True)
    return result


def fuzzy_span_result(text: str, reference: str) -> dict[str, object] | None:
    """Best fuzzy span via a rust backend; None means "use the python tier".

    Parity with core.hash_patch._best_fuzzy_span is proven per-release by
    tests/test_fuzzy_parity.py; any backend failure (including an older
    binary without the fuzzy_span op) falls back silently-but-warned.
    """
    backend = resolve_backend()
    if backend == "python":
        return None
    payload = {"text": text, "reference": reference}
    try:
        result = (
            _invoke_native("fuzzy_span", payload) if backend == "native" else _invoke_subprocess("fuzzy_span", payload)
        )
    except Exception as exc:
        _warn_fallback(backend, exc)
        return None
    if not result.get("ok", False):
        return None
    result["backend"] = backend
    return result


def _warn_fallback(backend: str, exc: Exception) -> None:
    """Graceful degradation stays, but silently 5x-slower scans do not."""
    global _fallback_warned
    if not _fallback_warned:
        _fallback_warned = True
        print(
            f"cluxion-supercoder: {backend} index backend failed ({type(exc).__name__}: {exc}); "
            "falling back to the slower python scanner for this process",
            file=sys.stderr,
        )


def _forced_backend() -> str | None:
    forced = os.environ.get(INDEX_BACKEND_ENV, "").strip().lower()
    return forced if forced in _BACKENDS else None


def _invoke_native(command: str, payload: dict[str, object]) -> dict[str, object]:
    native = _load_native()
    if native is None:
        raise RuntimeError("native backend forced but supercoder_index_native is not importable")
    raw = native.run(command, json.dumps(payload, ensure_ascii=False))
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
        timeout=30.0,
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
        if path.suffix not in extensions:
            continue
        rel = path.relative_to(root)
        # skip within-tree only, not root's own ancestry — parity with rust filter_entry (basename pruning)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        candidates.append(str(rel))
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
    "fuzzy_span_result",
    "index_available",
    "resolve_backend",
    "scan_repo",
    "scan_repo_result",
]
