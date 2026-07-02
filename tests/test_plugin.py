"""Tests for plugin registration and _wrap error handling."""

from __future__ import annotations

import json

from cluxion_agentplugin_supercoder import plugin, runner


def test_register_exposes_all_tools() -> None:
    class FakeCtx:
        def __init__(self):
            self.tools = {}
            self.commands = {}

        def register_tool(self, name, toolset=None, schema=None, handler=None, emoji=None):
            self.tools[name] = {"toolset": toolset, "schema": schema}

        def register_command(self, name, handler, description="", args_hint="", deliver="output"):
            self.commands[name] = {
                "handler": handler,
                "description": description,
                "args_hint": args_hint,
                "deliver": deliver,
            }

    ctx = FakeCtx()
    plugin.register(ctx)
    assert "supercoder" in ctx.commands
    assert "supercoder-doctor" in ctx.commands
    assert sorted(ctx.tools) == sorted(plugin.REGISTERED_TOOL_NAMES)
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


def test_wrap_coerces_none_args_to_clean_error() -> None:
    def any_handler(args):
        # if args was None, before fix: AttributeError on .get inside tools
        # now coerced to {}, so callback runs and may raise ValueError for missing keys
        if not args:
            raise ValueError("args required after coercion")
        return runner.ToolResult(True, {"echo": args})

    wrapped = plugin._wrap(any_handler)
    res = json.loads(wrapped(None))
    assert res == {"ok": False, "error": "args required after coercion"}


def test_repo_map_deterministic(tmp_path) -> None:
    # ensure deterministic output (no varying cache stats in payload)
    from cluxion_agentplugin_supercoder.core import repo_map as rm

    rm.clear_outline_cache()
    (tmp_path / "a.py").write_text("def f(): pass\n", encoding="utf-8")
    r1 = runner.repo_map_tool({"cwd": str(tmp_path)})
    r2 = runner.repo_map_tool({"cwd": str(tmp_path)})
    assert r1.to_json() == r2.to_json()
