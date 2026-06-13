from __future__ import annotations

import json
from collections.abc import Callable

from cluxion_agentplugin_supercoder import runner
from cluxion_agentplugin_supercoder.core.test_gate import suggest_test_commands
from cluxion_agentplugin_supercoder.schemas import (
    BRIEF_SCHEMA,
    CURSOR_MAP_SCHEMA,
    LINT_GATE_SCHEMA,
    PATCH_SCHEMA,
    PLAN_SCHEMA,
    READ_WINDOW_SCHEMA,
    REPO_MAP_SCHEMA,
    SYNTAX_GATE_SCHEMA,
    TEST_GATE_SCHEMA,
)


def register(ctx: object) -> None:
    ctx.register_tool(
        name="supercoder_plan", toolset="supercoder", schema=PLAN_SCHEMA, handler=_wrap(runner.plan), emoji="🧩"
    )
    ctx.register_tool(
        name="supercoder_read_window",
        toolset="supercoder",
        schema=READ_WINDOW_SCHEMA,
        handler=_wrap(runner.read_window_tool),
        emoji="📖",
    )
    ctx.register_tool(
        name="supercoder_patch",
        toolset="supercoder",
        schema=PATCH_SCHEMA,
        handler=_wrap(runner.patch_tool),
        emoji="🩹",
    )
    ctx.register_tool(
        name="supercoder_cursor_map",
        toolset="supercoder",
        schema=CURSOR_MAP_SCHEMA,
        handler=_wrap(runner.cursor_map_tool),
        emoji="🗺️",
    )
    ctx.register_tool(
        name="supercoder_syntax_gate",
        toolset="supercoder",
        schema=SYNTAX_GATE_SCHEMA,
        handler=_wrap(runner.syntax_gate_tool),
        emoji="🌳",
    )
    ctx.register_tool(
        name="supercoder_lint_gate",
        toolset="supercoder",
        schema=LINT_GATE_SCHEMA,
        handler=_wrap(runner.lint_gate_tool),
        emoji="🧹",
    )
    ctx.register_tool(
        name="supercoder_repo_map",
        toolset="supercoder",
        schema=REPO_MAP_SCHEMA,
        handler=_wrap(runner.repo_map_tool),
        emoji="🧭",
    )
    ctx.register_tool(
        name="supercoder_test_gate",
        toolset="supercoder",
        schema=TEST_GATE_SCHEMA,
        handler=_handle_test_gate,
        emoji="🧪",
    )
    ctx.register_tool(
        name="supercoder_brief", toolset="supercoder", schema=BRIEF_SCHEMA, handler=_handle_brief, emoji="📋"
    )


def _wrap(callback: Callable[[dict[str, object]], runner.ToolResult]) -> Callable[[dict[str, object]], str]:
    def handler(args: dict[str, object], **_: object) -> str:
        try:
            return callback(args).to_json()
        except (ValueError, FileNotFoundError, PermissionError) as exc:
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, sort_keys=True)

    return handler


def _handle_test_gate(args: dict[str, object], **_: object) -> str:
    from pathlib import Path

    raw_files = args.get("files_changed", [])
    files_changed = [str(item) for item in raw_files] if isinstance(raw_files, list) else []
    cwd_raw = str(args.get("cwd", ".")).strip() or "."
    payload = suggest_test_commands(
        files_changed,
        command=str(args.get("command", "")).strip() or None,
        cwd=Path(cwd_raw).expanduser().resolve(),
    )
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _handle_brief(args: dict[str, object], **_: object) -> str:
    return json.dumps(
        {
            "ok": True,
            "brief": {
                "files_changed": args.get("files_changed", []),
                "tests_run": args.get("tests_run", []),
                "verification_status": args.get("verification_status", "unknown_after_check"),
                "remaining_risks": args.get("remaining_risks", []),
            },
        },
        ensure_ascii=False,
        sort_keys=True,
    )


__all__ = ["register"]
