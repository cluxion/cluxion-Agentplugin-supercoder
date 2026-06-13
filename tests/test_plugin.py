from __future__ import annotations

from cluxion_agentplugin_supercoder import plugin


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
        "supercoder_lint_gate",
        "supercoder_patch",
        "supercoder_plan",
        "supercoder_read_window",
        "supercoder_repo_map",
        "supercoder_syntax_gate",
        "supercoder_test_gate",
    ]
    assert {tool["toolset"] for tool in ctx.tools.values()} == {"supercoder"}
