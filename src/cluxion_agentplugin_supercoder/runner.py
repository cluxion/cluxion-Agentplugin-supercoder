from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from cluxion_agentplugin_supercoder.core import lint_gate, repo_map, retry_loop, syntax_gate
from cluxion_agentplugin_supercoder.core.cursor import cursor_map, read_window
from cluxion_agentplugin_supercoder.core.hash_patch import apply_patch, revert_if_unchanged
from cluxion_agentplugin_supercoder.core.line_budget import budget_for, is_coding_task
from cluxion_agentplugin_supercoder.core.queue import plan_coding_task
from cluxion_agentplugin_supercoder.core.safety import pre_tool_gate
from cluxion_agentplugin_supercoder.core.test_gate import suggest_test_commands


def _int(v: object, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _invalid(message: str, hint: str) -> ToolResult:
    return ToolResult(False, {"error": "invalid_request", "message": message, "hint": hint})


def _files_changed(payload: Mapping[str, object]) -> list[str] | None:
    raw = payload.get("files_changed")
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError("files_changed must be a list")
    return [str(item) for item in raw if str(item).strip()]


def _without_ok(check: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in check.items() if key != "ok"}


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    payload: dict[str, object]

    def to_json(self) -> str:
        body = {"ok": self.ok, **self.payload}
        if not self.ok:
            body.setdefault("error", "command_failed")
            body.setdefault("message", str(body["error"]))
            body.setdefault("hint", "Check the request payload and retry.")
        return json.dumps(body, ensure_ascii=False, sort_keys=True)


def _workspace(payload: Mapping[str, object]) -> Path:
    cwd = str(payload.get("cwd", ".")).strip() or "."
    return Path(cwd).expanduser().resolve()


def plan(payload: Mapping[str, object]) -> ToolResult:
    prompt = str(payload.get("prompt", "")).strip()
    if not prompt:
        raise ValueError("prompt is required")
    if not is_coding_task(prompt):
        return ToolResult(True, {"mode": "bypass", "reason": "not_a_coding_task"})
    task_id = str(payload.get("task_id", "task-default"))
    queue = plan_coding_task(task_id, prompt)
    body: dict[str, object] = {
        "mode": "coding_queue",
        "task_id": task_id,
        "units": [
            {
                "id": unit.id,
                "goal": unit.goal,
                "priority": unit.priority,
                "status": unit.status.value,
                "dependencies": list(unit.dependencies),
            }
            for unit in queue.units
        ],
    }
    # Orientation for the host model: a compact map rides along with the
    # plan so small models stop guessing paths. Opt out with repo_map:false.
    if bool(payload.get("repo_map", True)):
        mapped = repo_map.build_repo_map(
            _workspace(payload),
            budget_chars=_int(payload.get("repo_map_budget_chars", 2_000), 2000),
        )
        if mapped.get("ok"):
            body["repo_map"] = {
                key: mapped[key]
                for key in ("map", "files_mapped", "files_omitted", "truncated", "backend", "fallback_from")
                if key in mapped
            }
    return ToolResult(True, body)


def read_window_tool(payload: Mapping[str, object]) -> ToolResult:
    root = _workspace(payload)
    rel = str(payload.get("path", "")).strip()
    gate = pre_tool_gate(
        "read_window",
        payload,
        workspace=root,
        stale_cursor=bool(payload.get("stale_cursor", False)),
    )
    if gate.decision == "block":
        return ToolResult(False, {"error": gate.reason})
    start = _int(payload.get("start_line", 1), 1)
    max_lines = _int(payload.get("max_lines", 120), 120)
    if start < 1:
        return _invalid("start_line must be >= 1", "Pass a positive integer.")
    if max_lines < 1:
        return _invalid("max_lines must be >= 1", "Pass a positive integer.")
    decision = budget_for("inspect", requested_lines=max_lines)
    if not decision.allowed:
        return ToolResult(False, {"error": decision.reason, "max_lines": decision.max_lines})
    window = read_window(root, rel, start_line=start, max_lines=max_lines, purpose=str(payload.get("purpose", "read")))
    return ToolResult(
        True,
        {
            "path": window.path,
            "start_line": window.start_line,
            "end_line": window.end_line,
            "content": window.content,
            "content_hash": window.content_hash,
            "file_hash": window.file_hash,
        },
    )


def patch_tool(payload: Mapping[str, object]) -> ToolResult:
    root = _workspace(payload)
    rel = str(payload.get("path", "")).strip()
    gate = pre_tool_gate("patch", payload, workspace=root, stale_cursor=bool(payload.get("stale_cursor", False)))
    if gate.decision == "block":
        return ToolResult(False, {"error": gate.reason})
    target = root / rel
    old_text = str(payload.get("old_text", ""))
    expected_file_hash = _expected_hash_from(payload)
    if not expected_file_hash:
        return _invalid(
            "expected_file_hash or expected_hash is required",
            "Pass the file_hash from read_window as expected_file_hash or expected_hash.",
        )
    try:
        result = apply_patch(
            target,
            old_text=old_text,
            new_text=str(payload.get("new_text", "")),
            expected_file_hash=expected_file_hash,
        )
    except UnicodeDecodeError as exc:
        return ToolResult(False, {"error": f"file is not valid UTF-8: {exc}"})
    except OSError as exc:
        return ToolResult(False, {"error": f"path is not a readable file: {exc}"})
    body: dict[str, object] = {
        "file_path": result.file_path,
        "strategy": result.strategy,
        "message": result.message,
        "expected_hash": result.expected_hash,
        "matched_hash": result.matched_hash,
        "similarity": result.similarity,
    }
    if result.success and bool(payload.get("syntax_gate", True)):
        # Verdict must use this patch's exact post-image; path only selects language.
        # Disk may already differ if a concurrent writer landed between commit and gate.
        check = syntax_gate.check_source(path=target, content=result._post_image)
        body["syntax"] = {key: check[key] for key in ("checked", "language", "valid", "error_count")}
        if check["checked"] and not check["valid"]:
            # L1 gate: the patch broke the file's syntax. Roll the file back
            # and surface the parse errors so the host model can retry.
            reverted = revert_if_unchanged(target, result.pre_image_raw, result.post_hash)
            strategy = "syntax_reverted" if reverted else "revert_failed"
            body["strategy"] = strategy
            body["message"] = (
                "patch reverted: result does not parse"
                if reverted
                else "patch revert refused: file changed after patch"
            )
            body["syntax_errors"] = check["errors"]
            advice = retry_loop.record_failure(str(root), rel, strategy, old_text=old_text)
            body["retry"] = advice.to_payload()
            return ToolResult(False, body)
    if result.success:
        retry_loop.record_success(str(root), rel)
        if bool(payload.get("lint_gate", True)):
            # L2 gate is suggest-only: findings never block or revert the patch.
            lint = lint_gate.check_file(target, cwd=root)
            if lint["checked"]:
                body["lint"] = {key: lint[key] for key in ("tool", "clean", "finding_count", "truncated")}
                if lint["findings"]:
                    body["lint"]["findings"] = lint["findings"]
    else:
        advice = retry_loop.record_failure(str(root), rel, result.strategy, old_text=old_text)
        body["retry"] = advice.to_payload()
    return ToolResult(result.success, body)


def cursor_map_tool(payload: Mapping[str, object]) -> ToolResult:
    root = _workspace(payload)
    paths = payload.get("paths")
    rel_paths = [str(item) for item in paths] if isinstance(paths, list) else None
    entries = cursor_map(root, paths=rel_paths)
    return ToolResult(True, {"entries": entries, "count": len(entries)})


def lint_gate_tool(payload: Mapping[str, object]) -> ToolResult:
    rel = str(payload.get("path", "")).strip()
    root = _workspace(payload)
    files = _files_changed(payload)
    if not rel and files is not None:
        results: list[dict[str, object]] = []
        ok = True
        for item in files:
            gate = pre_tool_gate("lint_gate", {"path": item}, workspace=root)
            if gate.decision == "block":
                ok = False
                results.append({"path": item, "error": gate.reason})
                continue
            check = lint_gate.check_file(root / item, cwd=root)
            ok = ok and bool(check.get("ok", False))
            results.append({"path": item, **_without_ok(check)})
        return ToolResult(ok, {"files": results})
    if not rel:
        raise ValueError("path is required")
    gate = pre_tool_gate("lint_gate", {"path": rel}, workspace=root)
    if gate.decision == "block":
        return ToolResult(False, {"error": gate.reason})
    check = lint_gate.check_file(root / rel, cwd=root)
    return ToolResult(bool(check.get("ok", False)), _without_ok(check))


def syntax_gate_tool(payload: Mapping[str, object]) -> ToolResult:
    content = payload.get("content")
    rel = str(payload.get("path", "")).strip()
    language = str(payload.get("language", "")).strip() or None
    files = _files_changed(payload)
    root = _workspace(payload)
    if content is None and not rel and files is not None:
        results: list[dict[str, object]] = []
        ok = True
        for item in files:
            gate = pre_tool_gate("syntax_gate", {"path": item}, workspace=root)
            if gate.decision == "block":
                ok = False
                results.append({"path": item, "error": gate.reason})
                continue
            check = syntax_gate.check_source(path=root / item, language=language)
            ok = ok and bool(check.get("ok", False))
            results.append({"path": item, **_without_ok(check)})
        return ToolResult(ok, {"files": results})
    if content is None and not rel:
        raise ValueError("content or path is required")
    if rel:
        gate = pre_tool_gate("syntax_gate", {"path": rel}, workspace=root)
        if gate.decision == "block":
            return ToolResult(False, {"error": gate.reason})
    check = syntax_gate.check_source(
        path=(root / rel) if rel else None,
        content=str(content) if content is not None else None,
        language=language,
    )
    return ToolResult(bool(check.get("ok", False)), _without_ok(check))


def repo_map_tool(payload: Mapping[str, object]) -> ToolResult:
    result = repo_map.build_repo_map(
        _workspace(payload),
        max_files=_int(payload.get("max_files", repo_map.DEFAULT_MAX_FILES), repo_map.DEFAULT_MAX_FILES),
        max_symbols_per_file=_int(
            payload.get("max_symbols_per_file", repo_map.DEFAULT_MAX_SYMBOLS_PER_FILE),
            repo_map.DEFAULT_MAX_SYMBOLS_PER_FILE,
        ),
        budget_chars=_int(payload.get("budget_chars", repo_map.DEFAULT_BUDGET_CHARS), repo_map.DEFAULT_BUDGET_CHARS),
    )
    return ToolResult(bool(result.get("ok", False)), _without_ok(result))


def test_gate_tool(payload: Mapping[str, object]) -> ToolResult:
    raw_files = payload.get("files_changed", [])
    files_changed = [str(item) for item in raw_files] if isinstance(raw_files, list) else []
    cwd_raw = str(payload.get("cwd", ".")).strip() or "."
    body = suggest_test_commands(
        files_changed,
        command=str(payload.get("command", "")).strip() or None,
        cwd=Path(cwd_raw).expanduser().resolve(),
    )
    return ToolResult(bool(body.get("ok", False)), {key: value for key, value in body.items() if key != "ok"})


def brief_tool(payload: Mapping[str, object]) -> ToolResult:
    return ToolResult(
        True,
        {
            "brief": {
                "files_changed": payload.get("files_changed", []),
                "tests_run": payload.get("tests_run", []),
                "verification_status": payload.get("verification_status", "unknown_after_check"),
                "remaining_risks": payload.get("remaining_risks", []),
            },
        },
    )


__all__ = [
    "ToolResult",
    "brief_tool",
    "cursor_map_tool",
    "lint_gate_tool",
    "patch_tool",
    "plan",
    "read_window_tool",
    "repo_map_tool",
    "syntax_gate_tool",
    "test_gate_tool",
]


def _expected_hash_from(payload: Mapping[str, object]) -> str:
    """Accept both spellings: input docs used expected_file_hash while the
    result object says expected_hash, and hosts mirroring the output name
    silently lost stale-write protection.

    Empty or whitespace-only values are treated as missing so patch_tool can
    reject before mutation when neither alias supplies a real hash.
    """
    for key in ("expected_hash", "expected_file_hash"):
        raw = payload.get(key)
        if raw is None:
            continue
        value = str(raw).strip()
        if value:
            return value
    return ""
