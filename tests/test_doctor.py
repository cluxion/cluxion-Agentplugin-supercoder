"""Tests for embedded doctor (determinism + cross-cutting checks)."""

import json
import subprocess
import time
from pathlib import Path

from cluxion_agentplugin_supercoder import plugin
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


def test_run_doctor_runs_probes_in_parallel():
    probes = {}
    for check_id in ("hermes_on_path", "hermes_version", "hermes_oneshot_flag"):

        def probe(ctx, check_id=check_id):
            time.sleep(0.05)
            return "pass", check_id

        probes[check_id] = probe

    start = time.perf_counter()
    result = run_doctor(
        cwd=Path.cwd(),
        catalog_path=_catalog_path(),
        probes=probes,
        plugin="supercoder",
        version="0.2.4",
    )
    elapsed = time.perf_counter() - start
    assert elapsed < 0.13
    assert {c.check_id: c.status for c in result.checks}["hermes_on_path"] == "pass"


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


def test_summary_critical_skip_is_degraded():
    from cluxion_agentplugin_supercoder.doctor.framework import CheckResult, DoctorResult

    checks = (
        CheckResult(check_id="a", category="c", severity="critical", status="skip", detail="no probe"),
        CheckResult(check_id="b", category="c", severity="high", status="pass", detail="ok"),
    )
    r = DoctorResult(plugin="p", version="0.2.4", checks=checks)
    assert r.summary == "degraded"
    assert r.ok is False


def test_summary_high_skip_is_degraded():
    from cluxion_agentplugin_supercoder.doctor.framework import CheckResult, DoctorResult

    checks = (
        CheckResult(check_id="a", category="c", severity="high", status="skip", detail="no probe"),
        CheckResult(check_id="b", category="c", severity="medium", status="pass", detail="ok"),
    )
    r = DoctorResult(plugin="p", version="0.2.4", checks=checks)
    assert r.summary == "degraded"
    assert r.ok is False


def test_summary_medium_or_low_skip_stays_ok():
    from cluxion_agentplugin_supercoder.doctor.framework import CheckResult, DoctorResult

    checks = (
        CheckResult(check_id="a", category="c", severity="medium", status="skip", detail="no probe"),
        CheckResult(check_id="b", category="c", severity="low", status="skip", detail="no probe"),
        CheckResult(check_id="c", category="c", severity="high", status="pass", detail="ok"),
    )
    r = DoctorResult(plugin="p", version="0.2.4", checks=checks)
    assert r.summary == "ok"
    assert r.ok is True


def test_summary_critical_fail_is_fail():
    from cluxion_agentplugin_supercoder.doctor.framework import CheckResult, DoctorResult

    checks = (
        CheckResult(check_id="a", category="c", severity="critical", status="fail", detail="broken"),
        CheckResult(check_id="b", category="c", severity="critical", status="skip", detail="no probe"),
    )
    r = DoctorResult(plugin="p", version="0.2.4", checks=checks)
    assert r.summary == "fail"
    assert r.ok is False


def test_critical_skip_marks_degraded_summary(monkeypatch):
    monkeypatch.setattr(
        "cluxion_agentplugin_supercoder.doctor.probes.shutil.which",
        lambda _: None,
    )
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
    assert statuses["hermes_on_path"] == "skip"
    assert statuses["hermes_oneshot_flag"] == "skip"
    assert result.summary == "degraded"
    assert result.ok is False
    payload = json.loads(render_json(result))
    assert payload["summary"] == "degraded"
    assert payload["ok"] is False


def test_hermes_on_path_skips_when_absent(monkeypatch):
    monkeypatch.setattr(
        "cluxion_agentplugin_supercoder.doctor.probes.shutil.which",
        lambda _: None,
    )
    status, detail = PROBES["hermes_on_path"](_doctor_ctx())
    assert status == "skip"
    assert "cannot verify" in detail


def test_hermes_oneshot_flag_skips_when_absent(monkeypatch):
    monkeypatch.setattr(
        "cluxion_agentplugin_supercoder.doctor.probes.shutil.which",
        lambda _: None,
    )
    status, detail = PROBES["hermes_oneshot_flag"](_doctor_ctx())
    assert status == "skip"
    assert "cannot verify" in detail


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


def test_healthy_install_ok_and_exit_zero(monkeypatch):
    monkeypatch.setattr(
        "cluxion_agentplugin_supercoder.doctor.probes.shutil.which",
        lambda name: "/usr/bin/hermes" if name == "hermes" else None,
    )

    def fake_run(cmd, **_kwargs):
        joined = " ".join(cmd)
        if "--version" in joined:
            return subprocess.CompletedProcess(cmd, 0, "Hermes Agent v1.0.0", "")
        if "--help" in joined:
            return subprocess.CompletedProcess(cmd, 0, "-z --oneshot", "")
        if "tools" in joined and "list" in joined:
            return subprocess.CompletedProcess(cmd, 0, "supercoder", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(
        "cluxion_agentplugin_supercoder.doctor.framework.subprocess.run",
        fake_run,
    )
    cat = _catalog_path()
    result = run_doctor(
        cwd=Path.cwd(),
        catalog_path=cat,
        probes=PROBES,
        plugin="supercoder",
        version="0.2.4",
    )
    statuses = {c.check_id: c.status for c in result.checks}
    assert statuses["hermes_contract_tool_registration"] == "pass"
    details = {c.check_id: c.detail for c in result.checks}
    assert details["hermes_contract_tool_registration"].startswith(f"{len(plugin.REGISTERED_TOOL_NAMES)} tools ")
    assert result.ok is True
    assert result.summary == "ok"
    payload = json.loads(render_json(result))
    assert payload["ok"] is True

    from cluxion_agentplugin_supercoder.cli import main

    assert main(["doctor"]) == 0


def test_genuine_failure_still_degraded_exit_one(monkeypatch):
    monkeypatch.setattr(
        "cluxion_agentplugin_supercoder.doctor.probes.shutil.which",
        lambda name: "/usr/bin/hermes" if name == "hermes" else None,
    )

    def fake_run(cmd, **_kwargs):
        joined = " ".join(cmd)
        if "--version" in joined:
            return subprocess.CompletedProcess(cmd, 0, "Hermes Agent v1.0.0", "")
        if "--help" in joined:
            return subprocess.CompletedProcess(cmd, 0, "-z --oneshot", "")
        if "tools" in joined and "list" in joined:
            return subprocess.CompletedProcess(cmd, 1, "", "tools unavailable")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(
        "cluxion_agentplugin_supercoder.doctor.framework.subprocess.run",
        fake_run,
    )
    result = run_doctor(
        cwd=Path.cwd(),
        catalog_path=_catalog_path(),
        probes=PROBES,
        plugin="supercoder",
        version="0.2.4",
    )
    statuses = {c.check_id: c.status for c in result.checks}
    assert statuses["toolset_valid"] == "fail"
    assert result.ok is False

    from cluxion_agentplugin_supercoder.cli import main

    assert main(["doctor"]) == 1


def test_doctor_json_keeps_stderr_silent(capsys) -> None:
    from cluxion_agentplugin_supercoder.cli import main

    assert main(["doctor", "--json"]) in (0, 1)
    captured = capsys.readouterr()
    assert json.loads(captured.out)["plugin"] == "supercoder"
    assert captured.err == ""


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


_CYCLE97_HIGH = (
    "backend_chain_operational",
    "syntax_gate_parser_available",
    "repo_map_budget_integrity",
    "json_error_handling_comprehensive",
)


def test_cycle97_high_probes_registered_and_pass():
    cat = _catalog_path()
    result = run_doctor(
        cwd=Path.cwd(),
        catalog_path=cat,
        probes=PROBES,
        plugin="supercoder",
        version="0.2.43",
    )
    statuses = {c.check_id: c.status for c in result.checks}
    for check_id in _CYCLE97_HIGH:
        assert check_id in PROBES, f"missing probe registration: {check_id}"
        assert statuses[check_id] == "pass", f"{check_id}: {statuses[check_id]}"
    # medium/low remain intentionally unregistered this cycle
    for check_id in (
        "utf8_file_readability",
        "concurrency_isolation",
        "subprocess_binary_present",
        "lint_gate_availability_graceful",
    ):
        assert check_id not in PROBES
        assert statuses[check_id] == "skip"


def test_missing_high_probe_marks_degraded():
    partial = {k: v for k, v in PROBES.items() if k not in _CYCLE97_HIGH}
    result = run_doctor(
        cwd=Path.cwd(),
        catalog_path=_catalog_path(),
        probes=partial,
        plugin="supercoder",
        version="0.2.43",
    )
    statuses = {c.check_id: c.status for c in result.checks}
    for check_id in _CYCLE97_HIGH:
        assert statuses[check_id] == "skip"
    assert result.summary == "degraded"
    assert result.ok is False
    payload = json.loads(render_json(result))
    assert payload["summary"] == "degraded"
    assert payload["ok"] is False


def test_backend_chain_operational_passes_on_python_backend(monkeypatch):
    from cluxion_agentplugin_supercoder import rust_bridge
    from cluxion_agentplugin_supercoder.doctor.probes import backend_chain_operational

    monkeypatch.setenv(rust_bridge.INDEX_BACKEND_ENV, "python")
    status, detail = backend_chain_operational(_doctor_ctx())
    assert status == "pass"
    assert "backend=" in detail


def test_backend_chain_operational_fails_on_ok_true_empty_entries(monkeypatch):
    """ok=True with entries=[] must fail — silent file-drop scanners are unhealthy."""
    from cluxion_agentplugin_supercoder.doctor import probes as probes_mod

    monkeypatch.setattr(
        "cluxion_agentplugin_supercoder.rust_bridge.scan_repo_result",
        lambda *_a, **_k: {"ok": True, "entries": []},
    )
    status, _detail = probes_mod.backend_chain_operational(_doctor_ctx())
    assert status == "fail"


def test_backend_chain_operational_fails_when_forced_subprocess_missing_bin(monkeypatch):
    """Forced subprocess with missing INDEX_BIN must FAIL via scan_repo_result status.

    Must not re-check binary presence itself — trust the typed ok/error result.
    """
    from cluxion_agentplugin_supercoder import rust_bridge
    from cluxion_agentplugin_supercoder.doctor.probes import backend_chain_operational

    monkeypatch.setenv(rust_bridge.INDEX_BACKEND_ENV, "subprocess")
    monkeypatch.setenv(
        rust_bridge.INDEX_BIN_ENV,
        "/nonexistent/cluxion-missing-supercoder-index",
    )
    status, detail = backend_chain_operational(_doctor_ctx())
    assert status == "fail"
    low = detail.lower()
    assert "ok" in low or "unavailable" in low or "backend" in low or "error" in low


def test_backend_chain_operational_fails_on_bad_scan(monkeypatch):
    from cluxion_agentplugin_supercoder.doctor import probes as probes_mod

    monkeypatch.setattr(
        "cluxion_agentplugin_supercoder.rust_bridge.scan_repo_result",
        lambda *_a, **_k: {
            "ok": False,
            "error": "backend_unavailable",
            "message": "forced fail for probe",
        },
    )
    status, detail = probes_mod.backend_chain_operational(_doctor_ctx())
    assert status == "fail"
    low = detail.lower()
    assert "ok" in low or "unavailable" in low or "backend" in low or "error" in low


def test_syntax_gate_parser_available_pass():
    from cluxion_agentplugin_supercoder.doctor.probes import syntax_gate_parser_available

    status, detail = syntax_gate_parser_available(_doctor_ctx())
    assert status == "pass"
    assert "python" in detail.lower() or "checked" in detail.lower()


def test_syntax_gate_parser_available_fails_when_unchecked(monkeypatch):
    from cluxion_agentplugin_supercoder.doctor import probes as probes_mod

    monkeypatch.setattr(
        "cluxion_agentplugin_supercoder.core.syntax_gate.check_source",
        lambda **_k: {"checked": False, "valid": True, "language": "python"},
    )
    status, _detail = probes_mod.syntax_gate_parser_available(_doctor_ctx())
    assert status == "fail"


def test_repo_map_budget_integrity_pass():
    from cluxion_agentplugin_supercoder.doctor.probes import repo_map_budget_integrity

    status, detail = repo_map_budget_integrity(_doctor_ctx())
    assert status == "pass"
    assert "mapped=" in detail
    # Must exercise the real omit path under the production 200-char floor.
    assert "omitted=" in detail
    assert "truncated=" in detail


def test_repo_map_budget_integrity_fails_on_contract_break(monkeypatch):
    from cluxion_agentplugin_supercoder.doctor import probes as probes_mod

    monkeypatch.setattr(
        "cluxion_agentplugin_supercoder.core.repo_map.build_repo_map",
        lambda *_a, **_k: {
            "ok": True,
            "files_mapped": 1,
            "files_omitted": 0,
            "files_scanned": 2,
            "truncated": False,
            "map": "short",
        },
    )
    status, detail = probes_mod.repo_map_budget_integrity(_doctor_ctx())
    assert status == "fail"
    assert "mapped" in detail.lower() or "scanned" in detail.lower()


def test_repo_map_budget_integrity_fails_when_map_over_budget(monkeypatch):
    """Plausible count invariants must not hide a map that exceeds the budget."""
    from cluxion_agentplugin_supercoder.doctor import probes as probes_mod

    monkeypatch.setattr(
        "cluxion_agentplugin_supercoder.core.repo_map.build_repo_map",
        lambda *_a, **_k: {
            "ok": True,
            "files_mapped": 2,
            "files_omitted": 3,
            "files_scanned": 5,
            "truncated": True,
            "map": "x" * 10_000,
        },
    )
    status, detail = probes_mod.repo_map_budget_integrity(_doctor_ctx())
    assert status == "fail"
    low = detail.lower()
    assert "budget" in low or "len" in low or "map" in low or "200" in low


def test_repo_map_budget_integrity_fails_when_no_omission(monkeypatch):
    """No-omission result is a false pass under a tight budget fixture."""
    from cluxion_agentplugin_supercoder.doctor import probes as probes_mod

    monkeypatch.setattr(
        "cluxion_agentplugin_supercoder.core.repo_map.build_repo_map",
        lambda *_a, **_k: {
            "ok": True,
            "files_mapped": 5,
            "files_omitted": 0,
            "files_scanned": 5,
            "truncated": False,
            "map": "short",
        },
    )
    status, detail = probes_mod.repo_map_budget_integrity(_doctor_ctx())
    assert status == "fail"
    low = detail.lower()
    assert "omit" in low or "truncated" in low


def test_json_error_handling_comprehensive_pass():
    from cluxion_agentplugin_supercoder.doctor.probes import json_error_handling_comprehensive

    status, detail = json_error_handling_comprehensive(_doctor_ctx())
    assert status == "pass"
    assert "json" in detail.lower() or "ok" in detail.lower()


def test_json_error_handling_comprehensive_fails_on_invalid_output(monkeypatch):
    from cluxion_agentplugin_supercoder.doctor import probes as probes_mod

    monkeypatch.setattr(
        "cluxion_agentplugin_supercoder.plugin._wrap",
        lambda _cb: (lambda _args: "not-json{"),
    )
    status, detail = probes_mod.json_error_handling_comprehensive(_doctor_ctx())
    assert status == "fail"
    assert "json" in detail.lower() or "parse" in detail.lower() or "raised" in detail.lower()


def test_cycle97_probe_exception_fails_not_skips(monkeypatch):
    from cluxion_agentplugin_supercoder.doctor import probes as probes_mod

    def boom(*_a, **_k):
        raise RuntimeError("probe boom")

    monkeypatch.setattr(
        "cluxion_agentplugin_supercoder.rust_bridge.resolve_backend",
        boom,
    )
    # Direct raise is converted by framework; probe itself must not swallow to skip.
    result = run_doctor(
        cwd=Path.cwd(),
        catalog_path=_catalog_path(),
        probes={"backend_chain_operational": probes_mod.backend_chain_operational},
        plugin="supercoder",
        version="0.2.43",
    )
    statuses = {c.check_id: c.status for c in result.checks}
    assert statuses["backend_chain_operational"] == "fail"
    assert statuses["backend_chain_operational"] != "skip"
