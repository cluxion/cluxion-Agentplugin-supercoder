from __future__ import annotations

from pathlib import Path

from cluxion_agentplugin_supercoder.core.cursor import read_window


def test_read_window_bounds(tmp_path: Path) -> None:
    path = tmp_path / "sample.py"
    path.write_text("\n".join(f"line{i}" for i in range(1, 201)), encoding="utf-8")
    window = read_window(tmp_path, "sample.py", start_line=10, max_lines=5)
    assert window.start_line == 10
    assert window.end_line == 14
    assert "line10" in window.content
