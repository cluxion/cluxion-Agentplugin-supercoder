"""Tests for embedded doctor (determinism + cross-cutting checks)."""

import json
import subprocess
from pathlib import Path

from cluxion_agentplugin_supercoder.doctor import (
    DoctorResult,
    render_json,
    run_doctor,
)
from cluxion_agentplugin_supercoder.doctor.framework import DoctorContext
from cluxion_agentplugin_supercoder.doctor.probes import PROBES


def _catalog_path() -> Path:
    import importlib.resources

    pkg = "cluxion_agentplugin_supercoder.doctor"
    return Path(str(importlib.resources.files(pkg).joinpath("catalog.json")))


def test_run_doctor_returns_result_and_deterministic():
    cat = _catalog_path()
    r1 = run_doctor(
        cwd=Path.cwd(),
        catalog_path=cat,
        probes=PROBES,
        plugin="supercoder",
        version="0.2.4",
    )
    assert isinstance(r1, DoctorResult)
    j1 = render_json(r1)
    r2 = run_doctor(
        cwd=Path.cwd(),
        catalog_path=cat,
        probes=PROBES,
        plugin="supercoder",
        version="0.2.4",
    )
    j2 = render_json(r2)
    assert j1 == j2  # byte identical
    # sorted by severity then id
    ids = [c.check_id for c in r1.checks]
    assert len(ids) > 0


def test_cross_cutting_checks_present():
    cat = _catalog_path()
    result = run_doctor(
        cwd=Path.cwd(),
        catalog_path=cat,
        probes=PROBES,
        plugin="supercoder",
        version="0.2.4",
    )
    statuses = {c.check_id: c.status for c in result.checks}
    for key in ("hermes_on_path", "entry_point_registered", "toolset_valid"):
        assert key in statuses
        assert statuses[key] in ("pass", "warn", "fail", "skip")


def test_new_probes_non_skip():
    cat = _catalog_path()
    result = run_doctor(
        cwd=Path.cwd(),
        catalog_path=cat,
        probes=PROBES,
        plugin="supercoder",
        version="0.2.4",
    )
    statuses = {c.check_id: c.status for c in result.checks}
    # assert at least two newly implemented return non-skip
    new_checks = [
        "hermes_requirements_installed",
        "repo_map_deterministic",
        "ruff_binary_discoverable",
        "file_hash_consistency",
    ]
    non_skip_count = sum(1 for k in new_checks if k in statuses and statuses[k] != "skip")
    assert non_skip_count >= 2, f"only {non_skip_count} new probes non-skip"


def test_probe_exception_becomes_fail():
    def bad_probe(ctx):
        raise RuntimeError("boom")

    result = run_doctor(
        cwd=Path.cwd(),
        catalog_path=_catalog_path(),
        probes={"hermes_on_path": bad_probe},
        plugin="supercoder",
        version="0.2.4",
    )
    statuses = {c.check_id: c.status for c in result.checks}
    assert statuses["hermes_on_path"] == "fail"


def test_warn_only_is_ok():
    # construct a result with only warn (no fail)
    from cluxion_agentplugin_supercoder.doctor.framework import CheckResult, DoctorResult

    checks = (CheckResult(check_id="x", category="c", severity="medium", status="warn", detail="w"),)
    r = DoctorResult(plugin="p", version="0.2.4", checks=checks)
    assert r.ok is True
    assert r.summary == "ok"
    # exit would be 0


def test_security_probes_registered_and_pass():
    cat = _catalog_path()
    result = run_doctor(
        cwd=Path.cwd(),
        catalog_path=cat,
        probes=PROBES,
        plugin="supercoder",
        version="0.2.4",
    )
    statuses = {c.check_id: c.status for c in result.checks}
    for check_id in (
        "path_security_secrets_blocked",
        "hermes_context_workspace_root",
        "patch_cursor_validity",
        "stale_cursor_protection_enforced",
        "handler_exception_coverage",
    ):
        assert statuses[check_id] == "pass", f"{check_id}: {statuses[check_id]}"


def test_critical_skip_marks_degraded_summary():
    cat = _catalog_path()
    partial = {k: v for k, v in PROBES.items() if k != "path_security_secrets_blocked"}
    result = run_doctor(
        cwd=Path.cwd(),
        catalog_path=cat,
        probes=partial,
        plugin="supercoder",
        version="0.2.4",
    )
    statuses = {c.check_id: c.status for c in result.checks}
    assert statuses["path_security_secrets_blocked"] == "skip"
    assert result.summary == "degraded"
    assert result.ok is False
    payload = json.loads(render_json(result))
    assert payload["summary"] == "degraded"
    assert payload["ok"] is False


def _doctor_ctx() -> DoctorContext:
    return DoctorContext(
        cwd=Path.cwd(),
        hermes_bin="hermes",
        run=lambda cmd: subprocess.CompletedProcess(cmd, 0, "", ""),
    )


def test_path_security_probe_detects_ungated_read(monkeypatch):
    from cluxion_agentplugin_supercoder.core.safety import SafetyDecision
    from cluxion_agentplugin_supercoder.doctor.probes import path_security_secrets_blocked

    def allow_all(*_args, **_kwargs):
        return SafetyDecision("allow", "bypassed")

    monkeypatch.setattr(
        "cluxion_agentplugin_supercoder.runner.pre_tool_gate",
        allow_all,
    )
    monkeypatch.setattr(
        "cluxion_agentplugin_supercoder.core.cursor.pre_tool_gate",
        allow_all,
    )
    status, _detail = path_security_secrets_blocked(_doctor_ctx())
    assert status == "fail"


def test_hermes_workspace_probe_detects_ungated_read(monkeypatch):
    from cluxion_agentplugin_supercoder.core.safety import SafetyDecision
    from cluxion_agentplugin_supercoder.doctor.probes import hermes_context_workspace_root

    def allow_all(*_args, **_kwargs):
        return SafetyDecision("allow", "bypassed")

    def always_contained(self, _other):
        return True

    monkeypatch.setattr(
        "cluxion_agentplugin_supercoder.runner.pre_tool_gate",
        allow_all,
    )
    monkeypatch.setattr(
        "cluxion_agentplugin_supercoder.core.cursor.pre_tool_gate",
        allow_all,
    )
    monkeypatch.setattr(Path, "is_relative_to", always_contained)
    status, _detail = hermes_context_workspace_root(_doctor_ctx())
    assert status == "fail"
