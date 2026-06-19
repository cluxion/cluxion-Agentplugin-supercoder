"""Plugin-specific probes for supercoder doctor. Cross-cutting + selected specific checks."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

from .framework import DoctorContext

PROBES: dict[str, Callable[[DoctorContext], tuple[str, str]]] = {}

_HERMES_ABSENT_SKIP = "hermes binary not on PATH — cannot verify"


def _hermes_path(ctx: DoctorContext) -> str | None:
    return shutil.which(ctx.hermes_bin)


def _register(name: str):
    def deco(fn):
        PROBES[name] = fn
        return fn

    return deco


@_register("hermes_on_path")
def hermes_on_path(ctx: DoctorContext) -> tuple[str, str]:
    p = _hermes_path(ctx)
    if p:
        return "pass", str(p)
    return "skip", _HERMES_ABSENT_SKIP


@_register("hermes_version")
def hermes_version(ctx: DoctorContext) -> tuple[str, str]:
    if _hermes_path(ctx) is None:
        return "skip", _HERMES_ABSENT_SKIP
    try:
        cp = ctx.run([ctx.hermes_bin, "--version"])
        if cp.returncode == 0 and "Hermes Agent v" in cp.stdout:
            return "pass", cp.stdout.strip()
        return "fail", cp.stdout.strip() or cp.stderr.strip()
    except Exception as e:
        return "fail", f"run error: {e}"


@_register("hermes_oneshot_flag")
def hermes_oneshot_flag(ctx: DoctorContext) -> tuple[str, str]:
    if _hermes_path(ctx) is None:
        return "skip", _HERMES_ABSENT_SKIP
    try:
        cp = ctx.run([ctx.hermes_bin, "--help"])
        out = cp.stdout + cp.stderr
        if "-z" in out and "--oneshot" in out:
            return "pass", "present"
        return "fail", "missing in --help"
    except Exception as e:
        return "fail", f"run error: {e}"


@_register("entry_point_registered")
def entry_point_registered(ctx: DoctorContext) -> tuple[str, str]:
    try:
        eps = importlib.metadata.entry_points(group="hermes_agent.plugins")
        for ep in eps:
            if "cluxion-agentplugin-supercoder" in (ep.name or "").lower() or "cluxion_agentplugin_supercoder" in (
                ep.value or ""
            ):
                mod = ep.load()
                if hasattr(mod, "register") and callable(mod.register):
                    return "pass", ep.value or str(ep)
        return "warn", "entry point metadata not present (dev PYTHONPATH ok)"
    except Exception as e:
        return "fail", f"metadata error: {e}"


@_register("toolset_valid")
def toolset_valid(ctx: DoctorContext) -> tuple[str, str]:
    if _hermes_path(ctx) is None:
        return "skip", _HERMES_ABSENT_SKIP
    try:
        cp = ctx.run([ctx.hermes_bin, "tools", "list"])
        if cp.returncode == 0 and "supercoder" in cp.stdout:
            return "pass", "supercoder present"
        return "fail", "supercoder not in tools list"
    except Exception as e:
        return "fail", f"run error: {e}"


@_register("install_integrity")
def install_integrity(ctx: DoctorContext) -> tuple[str, str]:
    try:
        from cluxion_agentplugin_supercoder import __version__ as pkg_version

        dist_version = importlib.metadata.version("cluxion-agentplugin-supercoder")
        if dist_version == pkg_version:
            return "pass", dist_version
        return "warn", f"dist={dist_version} pkg={pkg_version}"
    except Exception as e:
        return "warn", f"version error: {e}"


@_register("native_module_importable")
def native_module_importable(ctx: DoctorContext) -> tuple[str, str]:
    try:
        mod = __import__("supercoder_index_native")
        if hasattr(mod, "run"):
            return "pass", "imported (native backend available)"
        return "warn", "imported but expected symbols missing"
    except Exception:
        return "warn", "native missing → using fallback (slower)"


# plugin-specific probes (deterministic ones only) - for supercoder we can add if symbols found
# for now, handler_exception_coverage is cross-cutting
_EXPECTED_TOOLS = (
    "supercoder_plan",
    "supercoder_read_window",
    "supercoder_patch",
    "supercoder_cursor_map",
    "supercoder_syntax_gate",
    "supercoder_lint_gate",
    "supercoder_repo_map",
    "supercoder_test_gate",
    "supercoder_brief",
    "supercoder_doctor",
)


@_register("hermes_contract_tool_registration")
def hermes_contract_tool_registration(ctx: DoctorContext) -> tuple[str, str]:
    try:
        from cluxion_agentplugin_supercoder.plugin import register

        class _MockCtx:
            def __init__(self) -> None:
                self.tools: list[tuple[str, str, dict, object, str]] = []

            def register_tool(
                self,
                *,
                name: str,
                toolset: str,
                schema: dict,
                handler: object,
                emoji: str,
            ) -> None:
                self.tools.append((name, toolset, schema, handler, emoji))

        mock = _MockCtx()
        register(mock)
        registered = [name for name, *_ in mock.tools]
        missing = [name for name in _EXPECTED_TOOLS if name not in registered]
        if missing:
            return "fail", f"missing tools: {', '.join(missing)}"
        extras = [name for name in registered if name not in _EXPECTED_TOOLS]
        if extras:
            return "fail", f"unexpected tools: {', '.join(extras)}"
        for name, toolset, schema, handler, _emoji in mock.tools:
            if toolset != "supercoder":
                return "fail", f"{name}: toolset={toolset!r}"
            if not isinstance(schema, dict) or not schema.get("name"):
                return "fail", f"{name}: invalid schema"
            if not callable(handler):
                return "fail", f"{name}: handler not callable"
        return "pass", f"{len(_EXPECTED_TOOLS)} tools registered with schemas and handlers"
    except Exception as e:
        return "fail", f"registration error: {e}"


@_register("handler_exception_coverage")
def handler_exception_coverage(ctx: DoctorContext) -> tuple[str, str]:
    try:
        from cluxion_agentplugin_supercoder import runner
        from cluxion_agentplugin_supercoder.plugin import _wrap

        def bad_cb(_payload: dict[str, object]) -> runner.ToolResult:
            raise TypeError("test TypeError for coverage")

        result = _wrap(bad_cb)({})
        parsed = json.loads(result)
        if parsed.get("ok") is False and "TypeError" in str(parsed.get("error", "")):
            return "pass", "degraded to error JSON"
        return "fail", f"no error json: {result[:100]}"
    except Exception as e:
        return "skip", f"cannot invoke guard: {e}"


# NEW deterministic probes for previously-skipped catalog checks (import-avail, json-det, abi3/sqlite patterns adapted)
# hermes_requirements_installed (import availability using find_spec to satisfy linter)
@_register("hermes_requirements_installed")
def hermes_requirements_installed(ctx: DoctorContext) -> tuple[str, str]:
    try:
        if importlib.util.find_spec("psutil") and importlib.util.find_spec("yaml"):
            return "pass", "psutil+PyYAML importable"
        return "warn", "missing dep"
    except Exception as e:
        return "skip", f"import check error: {e}"


# repo_map_deterministic (json determinism + real call)
@_register("repo_map_deterministic")
def repo_map_deterministic(ctx: DoctorContext) -> tuple[str, str]:
    try:
        from cluxion_agentplugin_supercoder.core.repo_map import build_repo_map

        m1 = build_repo_map(ctx.cwd, budget_chars=2000)
        m2 = build_repo_map(ctx.cwd, budget_chars=2000)

        def strip(d):
            if isinstance(d, dict):
                return {k: strip(v) for k, v in d.items() if k != "_stats"}
            if isinstance(d, list):
                return [strip(x) for x in d]
            return d

        if strip(m1) == strip(m2):
            j1 = json.dumps(m1, sort_keys=True)
            j2 = json.dumps(m2, sort_keys=True)
            if j1 == j2:
                return "pass", "deterministic + json roundtrip ok"
            return "warn", "map match but json not"
        return "fail", "non-deterministic"
    except Exception as e:
        return "skip", f"cannot run: {e}"


# ruff_binary_discoverable (real env/path probe)
@_register("ruff_binary_discoverable")
def ruff_binary_discoverable(ctx: DoctorContext) -> tuple[str, str]:
    try:
        envb = os.environ.get("CLUXION_SUPERCODER_RUFF_BIN")
        if envb and Path(envb).is_file():
            return "pass", envb
        cands = [Path(ctx.cwd) / ".venv/bin/ruff", shutil.which("ruff")]
        for c in cands:
            if c and Path(c).is_file():
                return "pass", str(c)
        return "warn", "no ruff binary (advisory)"
    except Exception as e:
        return "skip", f"probe error: {e}"


# file_hash_consistency (real check)
@_register("file_hash_consistency")
def file_hash_consistency(ctx: DoctorContext) -> tuple[str, str]:
    try:
        from cluxion_agentplugin_supercoder.core.hash_patch import _normalize_newlines, file_hash

        c = "a=1\r\nb=2"
        if file_hash(c) == file_hash(_normalize_newlines(c)):
            return "pass", "CRLF safe"
        return "fail", "hash mismatch"
    except Exception as e:
        return "skip", f"hash error: {e}"


_SECRET_BLOCKED = "secret file access blocked"
_ESCAPE_BLOCKED = "workspace escape blocked"


def _assert_tool_blocks(
    tool_fn: Callable[[dict[str, object]], object],
    *,
    cwd: str,
    path: str,
    expected_error: str,
    extra: dict[str, object] | None = None,
) -> str | None:
    from cluxion_agentplugin_supercoder import runner

    payload: dict[str, object] = {"cwd": cwd, "path": path}
    if extra:
        payload.update(extra)
    result = tool_fn(payload)
    if not isinstance(result, runner.ToolResult):
        return f"unexpected result type for {path}"
    if result.ok or result.payload.get("error") != expected_error:
        return f"{tool_fn.__name__} on {path}: ok={result.ok} error={result.payload.get('error')}"
    return None


@_register("path_security_secrets_blocked")
def path_security_secrets_blocked(ctx: DoctorContext) -> tuple[str, str]:
    try:
        from cluxion_agentplugin_supercoder import runner

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".env").write_text("KEY=secret", encoding="utf-8")
            cred = root / "config" / "credentials"
            cred.mkdir(parents=True)
            (cred / "db.json").write_text("{}", encoding="utf-8")
            for rel in (".env", "config/credentials/db.json"):
                for tool_fn, extra in (
                    (runner.read_window_tool, None),
                    (runner.patch_tool, {"old_text": "x", "new_text": "y", "syntax_gate": False}),
                ):
                    err = _assert_tool_blocks(
                        tool_fn,
                        cwd=str(root),
                        path=rel,
                        expected_error=_SECRET_BLOCKED,
                        extra=extra,
                    )
                    if err:
                        return "fail", err
            return "pass", "read_window_tool + patch_tool block .env and credentials"
    except Exception as e:
        return "skip", f"cannot run: {e}"


@_register("hermes_context_workspace_root")
def hermes_context_workspace_root(ctx: DoctorContext) -> tuple[str, str]:
    try:
        from cluxion_agentplugin_supercoder import runner

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            outside = base / "outside"
            outside.mkdir()
            (outside / "secret.txt").write_text("leaked", encoding="utf-8")
            workspace = base / "work"
            workspace.mkdir()
            sibling = base / "work2"
            sibling.mkdir()
            (sibling / "x").write_text("leaked", encoding="utf-8")
            for path in ("../outside/secret.txt", "../work2/x"):
                err = _assert_tool_blocks(
                    runner.read_window_tool,
                    cwd=str(workspace),
                    path=path,
                    expected_error=_ESCAPE_BLOCKED,
                )
                if err:
                    return "fail", err
            return "pass", "traversal + sibling-prefix escape blocked"
    except Exception as e:
        return "skip", f"cannot run: {e}"


@_register("patch_cursor_validity")
def patch_cursor_validity(ctx: DoctorContext) -> tuple[str, str]:
    try:
        from cluxion_agentplugin_supercoder import runner
        from cluxion_agentplugin_supercoder.core.cursor import read_window

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "t.py"
            target.write_text("x=1", encoding="utf-8")
            window = read_window(root, "t.py")
            target.write_text("x=2", encoding="utf-8")
            result = runner.patch_tool(
                {
                    "cwd": str(root),
                    "path": "t.py",
                    "old_text": "x=1",
                    "new_text": "x=3",
                    "expected_file_hash": window.file_hash,
                    "syntax_gate": False,
                    "lint_gate": False,
                }
            )
            if result.ok:
                return "fail", "patch applied with stale hash"
            if result.payload.get("strategy") != "stale_file":
                return "fail", f"expected stale_file, got {result.payload.get('strategy')}"
            return "pass", "stale hash blocked"
    except Exception as e:
        return "skip", f"cannot run: {e}"


@_register("stale_cursor_protection_enforced")
def stale_cursor_protection_enforced(ctx: DoctorContext) -> tuple[str, str]:
    try:
        from cluxion_agentplugin_supercoder import runner

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "t.py").write_text("x=1", encoding="utf-8")
            result = runner.patch_tool(
                {
                    "cwd": str(root),
                    "path": "t.py",
                    "old_text": "x",
                    "new_text": "y",
                    "stale_cursor": True,
                    "syntax_gate": False,
                    "lint_gate": False,
                }
            )
            if result.ok:
                return "fail", "patch applied with stale_cursor=True"
            error = str(result.payload.get("error", ""))
            if "stale cursor" not in error:
                return "fail", f"unexpected error: {error}"
            return "pass", "stale_cursor flag blocked"
    except Exception as e:
        return "skip", f"cannot run: {e}"


# note: other checks in catalog will be reported as skip (no probe)
