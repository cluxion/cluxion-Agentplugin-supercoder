from __future__ import annotations

from pathlib import Path

from cluxion_agentplugin_supercoder import rust_bridge
from cluxion_agentplugin_supercoder.core import hash_patch
from cluxion_agentplugin_supercoder.core.repo_map import build_repo_map


def test_repo_map_skips_count_walk_when_under_cap(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("y = 2\n", encoding="utf-8")
    calls = {"count": 0}

    def _counting(*args: object, **kwargs: object) -> int:
        calls["count"] += 1
        return 2

    monkeypatch.setattr(rust_bridge, "count_scan_candidates", _counting)
    result = build_repo_map(tmp_path, max_files=64)
    assert result["ok"] is True
    assert calls["count"] == 0


def test_repo_map_counts_only_when_cap_reached(tmp_path: Path, monkeypatch) -> None:
    for index in range(3):
        (tmp_path / f"f{index}.py").write_text("x = 1\n", encoding="utf-8")
    calls = {"count": 0}

    def _counting(*args: object, **kwargs: object) -> int:
        calls["count"] += 1
        return 3

    monkeypatch.setattr(rust_bridge, "count_scan_candidates", _counting)
    build_repo_map(tmp_path, max_files=2)
    assert calls["count"] == 1


def test_patch_lock_lives_outside_the_workspace(tmp_path: Path) -> None:
    target = tmp_path / "module.py"
    target.write_text("x = 1\n", encoding="utf-8")
    lock = hash_patch._lock_path(target)
    assert not str(lock).startswith(str(tmp_path))
    assert lock.parent.name == "cluxion-supercoder-locks"


def test_native_import_is_lazy() -> None:
    # resolve_backend triggers resolution; before that the module must not
    # have imported the wheel (guard against regressing to eager import).
    assert hasattr(rust_bridge, "_load_native")
    rust_bridge._native_resolved = False
    rust_bridge._native = None
    backend = rust_bridge.resolve_backend()
    assert backend in ("native", "subprocess", "python")
    assert rust_bridge._native_resolved is True
