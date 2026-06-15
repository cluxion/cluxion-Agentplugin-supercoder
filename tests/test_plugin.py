from __future__ import annotations

import json

from cluxion_agentplugin_supercoder import plugin, runner


class FakeContext:
    def __init__(self) -> None:
        self.tools: dict[str, dict[str, object]] = {}

    def register_tool(
        self, *, name: str, toolset: str, schema: dict[str, object], handler: object, emoji: str = ""
    ) -> None:
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler, "emoji": emoji}


def test_register_tools() -> None:
    ctx = FakeContext()
    plugin.register(ctx)
    assert sorted(ctx.tools) == [
        "supercoder_brief",
        "supercoder_cursor_map",
        "supercoder_doctor",
        "supercoder_lint_gate",
        "supercoder_patch",
        "supercoder_plan",
        "supercoder_read_window",
        "supercoder_repo_map",
        "supercoder_syntax_gate",
        "supercoder_test_gate",
    ]
    assert {tool["toolset"] for tool in ctx.tools.values()} == {"supercoder"}


def test_wrap_catches_isadirectory_and_typeerror() -> None:
    def bad_handler(args):
        if args.get("mode") == "dir":
            raise IsADirectoryError("path is a directory")
        raise TypeError("bad int coercion")

    wrapped = plugin._wrap(bad_handler)
    res_dir = json.loads(wrapped({"mode": "dir"}))
    assert res_dir == {"ok": False, "error": "path is a directory"}
    res_type = json.loads(wrapped({}))
    assert res_type == {"ok": False, "error": "bad int coercion"}


def test_repo_map_deterministic(tmp_path) -> None:
    # ensure deterministic output (no varying cache stats in payload)
    from cluxion_agentplugin_supercoder.core import repo_map as rm

    rm.clear_outline_cache()
    (tmp_path / "a.py").write_text("def f(): pass\n", encoding="utf-8")
    r1 = runner.repo_map_tool({"cwd": str(tmp_path)})
    r2 = runner.repo_map_tool({"cwd": str(tmp_path)})
    assert r1.to_json() == r2.to_json()
