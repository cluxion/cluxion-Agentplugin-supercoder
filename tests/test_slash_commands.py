from __future__ import annotations

import json

from cluxion_agentplugin_supercoder import runner
from cluxion_agentplugin_supercoder.slash_commands import (
    SUPERCODER_HELP,
    build_supercoder_directive,
    handle_supercoder,
)


def test_supercoder_help_without_args() -> None:
    assert "supercoder_plan" in handle_supercoder("")
    assert handle_supercoder("help") == SUPERCODER_HELP


def test_build_directive_includes_task() -> None:
    body = {"mode": "coding_queue", "units": [{"id": "u1", "goal": "fix tests"}]}
    text = build_supercoder_directive("fix auth tests", body)
    assert "[SUPERCODER MODE]" in text
    assert "fix auth tests" in text
    assert "supercoder_plan" in text


def test_handle_supercoder_bypass(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    out = handle_supercoder("what is the weather today?")
    assert "not a coding task" in out


def test_handle_supercoder_skips_repo_map_for_fast_dispatch(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    captured: list[dict[str, object]] = []

    def _spy(payload: dict[str, object]) -> runner.ToolResult:
        captured.append(dict(payload))
        return runner.plan(payload)

    monkeypatch.setattr(runner, "plan", _spy)
    handle_supercoder("implement pagination in the users API")
    assert captured
    assert captured[0].get("repo_map") is False


def test_handle_supercoder_returns_directive(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.py").write_text("x=1\n", encoding="utf-8")
    out = handle_supercoder("refactor a.py and fix the failing unit tests")
    assert "[SUPERCODER MODE]" in out
    plan = json.loads(out.split("supercoder_plan:\n", 1)[1])
    assert plan.get("mode") == "coding_queue"