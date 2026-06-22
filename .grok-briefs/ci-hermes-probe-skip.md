# Task: doctor hermes-dependent critical probes must SKIP when hermes absent + env-independent test (CI fix #2)

## Context
CI (no `hermes` binary on PATH) STILL fails: `tests/test_doctor.py` test
`test_critical_skip_marks_degraded_summary` expects summary=="degraded" but gets "fail".
Root cause: critical probes `hermes_on_path` (probes.py:27) and `hermes_oneshot_flag` (probes.py:46)
FAIL when hermes isn't on PATH (the CI build runner has no hermes). That makes summary "fail" instead
of "degraded". The sibling ultracode plugin already fixed this by skipping hermes probes when hermes
is absent — MIRROR that approach here.

## Fix
1. `doctor/probes.py`: `hermes_on_path` and `hermes_oneshot_flag` (and any probe needing the hermes
   binary): when `shutil.which(ctx.hermes_bin)` is None, return `("skip", "hermes binary not on PATH
   — cannot verify")` instead of fail. FAIL only when hermes IS present but the contract is violated.
2. Make `tests/test_doctor.py`'s honest-summary test ENVIRONMENT-INDEPENDENT — it must pass whether or
   not hermes/native exist on the machine. Best: unit-test the summary computation directly (feed a
   controlled statuses dict — one critical "skip" + rest "pass" → assert "degraded"; one critical
   "fail" → assert "fail"). If it runs the real doctor, monkeypatch `shutil.which` so hermes appears
   absent and assert degraded (not fail).

## Invariants (MUST hold)
- hermes absent → hermes probes SKIP → summary "degraded". hermes present + valid → PASS.
- Critical SKIP → "degraded"; critical FAIL → "fail" (unchanged). Security/cursor probes UNCHANGED.

## Tests (CRITICAL — CI is a NO-hermes, NO-native environment)
- `uv run pytest` must be green REGARDLESS of whether hermes/native are installed. The honest-summary
  test must NOT depend on the machine having hermes. (If unsure, make it a pure unit test of the
  summary function.)
- `uv run ruff check .` clean.

## Out of scope
- No version bump. No change to the P0 security fix or DB/other probes beyond skip-on-absence.

## Done
hermes-dependent probes skip when hermes absent; honest-summary test is environment-independent;
pytest green with no hermes/native. Concise diff.
