"""Cursor logic — bounded file windows with hash verification."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cluxion_agentplugin_supercoder.core.hash_patch import file_hash


@dataclass(frozen=True)
class LineWindow:
    path: str
    start_line: int
    end_line: int
    content: str
    content_hash: str
    file_hash: str
    purpose: str = "read"


def read_window(
    root: Path,
    rel_path: str,
    *,
    start_line: int = 1,
    max_lines: int = 120,
    purpose: str = "read",
) -> LineWindow:
    path = (root / rel_path).resolve()
    if not path.exists():
        raise FileNotFoundError(rel_path)
    if not str(path).startswith(str(root.resolve())):
        raise PermissionError("path escapes workspace root")
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    start = max(1, start_line)
    end = min(len(lines), start + max_lines - 1)
    if start > len(lines):
        excerpt = ""
        end = start
    else:
        excerpt = "\n".join(lines[start - 1 : end])
    return LineWindow(
        path=rel_path,
        start_line=start,
        end_line=end,
        content=excerpt,
        content_hash=file_hash(excerpt),
        file_hash=file_hash(text),
        purpose=purpose,
    )


def cursor_map(root: Path, *, paths: list[str] | None = None, max_files: int = 64) -> list[dict[str, object]]:
    if paths is None:
        from cluxion_agentplugin_supercoder.rust_bridge import scan_repo

        scanned = scan_repo(root, max_files=max_files)
        return [{**entry, "purpose": "index"} for entry in scanned]
    entries: list[dict[str, object]] = []
    for rel in paths[:max_files]:
        path = root / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        lines = text.count("\n") + (1 if text else 0)
        entries.append(
            {
                "path": rel,
                "file_hash": file_hash(text),
                "total_lines": lines,
                "purpose": "index",
            }
        )
    return entries


__all__ = ["LineWindow", "cursor_map", "read_window"]
