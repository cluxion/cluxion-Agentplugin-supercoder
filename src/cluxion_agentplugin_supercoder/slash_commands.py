"""Hermes slash commands for supercoder (/supercoder, /supercoder-doctor)."""

from __future__ import annotations

import json
from pathlib import Path

from cluxion_agentplugin_supercoder import runner

SUPERCODER_HELP = """\
/supercoder <task> — Enter supercoder coding mode

Runs supercoder_plan and sends a coding harness directive to the agent.

Workflow the agent must follow:
  supercoder_plan → supercoder_read_window → supercoder_patch
  → supercoder_syntax_gate → supercoder_lint_gate → supercoder_test_gate → supercoder_brief

Examples:
  /supercoder fix failing auth tests in src/auth/
  /supercoder add pagination to the users API with tests

Notes:
  - Patches are hash-verified; empty old_text is rejected immediately
  - Slash dispatch skips repo_map (fast); agent calls supercoder_plan for map
  - Diagnostics: /supercoder-doctor
"""

# Hermes Desktop gateway RPC times out at 30s; repo_map on large cwds can exceed that.
_SLASH_PLAN_KWARGS: dict[str, object] = {"repo_map": False}


def build_supercoder_directive(task: str, plan_payload: dict[str, object]) -> str:
    plan_block = json.dumps(plan_payload, ensure_ascii=False, indent=2)
    return (
        "[SUPERCODER MODE]\n"
        "Use the supercoder toolset for all code changes in this task.\n\n"
        f"Task: {task}\n\n"
        "Required sequence:\n"
        "1. Use the plan below; call supercoder_plan for a fresh repo_map if needed\n"
        "2. supercoder_read_window before each edit\n"
        "3. supercoder_patch only with verified old_text hashes\n"
        "4. supercoder_syntax_gate → supercoder_lint_gate → supercoder_test_gate after edits\n"
        "5. supercoder_brief with files_changed, tests_run, verification_status\n\n"
        f"supercoder_plan:\n{plan_block}"
    )


def handle_supercoder(raw_args: str, ctx: object | None = None) -> str:
    del ctx  # Hermes deliver=agent routes the return value to the agent turn
    task = raw_args.strip()
    if not task or task.lower() in {"help", "-h", "--help"}:
        return SUPERCODER_HELP
    try:
        result = runner.plan(
            {"prompt": task, "cwd": str(Path.cwd()), **_SLASH_PLAN_KWARGS},
        )
        payload = json.loads(result.to_json())
        if not payload.get("ok"):
            return f"supercoder error: {payload.get('error', 'plan failed')}"
        body = payload.get("result", payload)
        if not isinstance(body, dict):
            body = {"plan": body}
        if body.get("mode") == "bypass":
            return (
                f"supercoder: not a coding task ({body.get('reason', 'bypass')}).\n"
                "Rephrase as a concrete code change request."
            )
        return build_supercoder_directive(task, body)
    except Exception as exc:
        return f"supercoder error: {exc}"


__all__ = [
    "SUPERCODER_HELP",
    "build_supercoder_directive",
    "handle_supercoder",
]