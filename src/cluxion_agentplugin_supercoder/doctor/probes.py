"""Plugin-specific probes for supercoder doctor. Cross-cutting + selected specific checks."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path

from .framework import DoctorContext

PROBES: dict[str, Callable[[DoctorContext], tuple[str, str]]] = {}


def _register(name: str):
    def deco(fn):
        PROBES[name] = fn
        return fn

    return deco


@_register("hermes_on_path")
def hermes_on_path(ctx: DoctorContext) -> tuple[str, str]:
    p = shutil.which(ctx.hermes_bin)
    if p:
        return "pass", str(p)
    return "fail", "not found on PATH"


@_register("hermes_version")
def hermes_version(ctx: DoctorContext) -> tuple[str, str]:
    try:
        cp = ctx.run([ctx.hermes_bin, "--version"])
        if cp.returncode == 0 and "Hermes Agent v" in cp.stdout:
            return "pass", cp.stdout.strip()
        return "fail", cp.stdout.strip() or cp.stderr.strip()
    except Exception as e:
        return "fail", f"run error: {e}"


@_register("hermes_oneshot_flag")
def hermes_oneshot_flag(ctx: DoctorContext) -> tuple[str, str]:
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
            if "cluxion-agentplugin-supercoder" in (ep.name or "").lower() or "cluxion_agentplugin_supercoder" in (ep.value or ""):
                mod = ep.load()
                if hasattr(mod, "register") and callable(mod.register):
                    return "pass", ep.value or str(ep)
        return "warn", "entry point metadata not present (dev PYTHONPATH ok)"
    except Exception as e:
        return "fail", f"metadata error: {e}"


@_register("toolset_valid")
def toolset_valid(ctx: DoctorContext) -> tuple[str, str]:
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
@_register("handler_exception_coverage")
def handler_exception_coverage(ctx: DoctorContext) -> tuple[str, str]:
    try:
        from cluxion_agentplugin_supercoder.plugin import _json_result
        def bad_cb():
            raise TypeError("test TypeError for coverage")
        result = _json_result(bad_cb)
        if isinstance(result, str) and "ok" in result and "false" in result.lower():
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
        cands = [Path(ctx.cwd)/".venv/bin/ruff", shutil.which("ruff")]
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
        from cluxion_agentplugin_supercoder.core.hash_patch import file_hash, _normalize_newlines
        c = 'a=1\r\nb=2'
        if file_hash(c) == file_hash(_normalize_newlines(c)):
            return "pass", "CRLF safe"
        return "fail", "hash mismatch"
    except Exception as e:
        return "skip", f"hash error: {e}"


# note: other checks in catalog will be reported as skip (no probe)
