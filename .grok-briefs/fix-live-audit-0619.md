# Task: supercoder — fix 3 live-audit defects (1 P2 false-degraded doctor + 2 P3)

## Context
Installed build is 0.2.9. A live adversarial audit (running the installed site-packages build) confirmed 3 REAL defects. Fix all in repo `src/`. Do NOT regress the 15 live-verified working functions. Do NOT bump the version in pyproject (deploy handled separately). Do NOT touch `.grok-briefs/`.

## Defect 1 (P2 — FALSE HEALTH ALARM): `cluxion-supercoder doctor` reports 'degraded' / exit 1 on a fully-healthy install
Running the plugin's own self-doctor on a correct install yields a red 'degraded' verdict and a non-zero exit status even though every real probe passes and the plugin is fully functional. Cause: a catalog check (`hermes_contract_tool_registration`) is marked 'critical' but has NO registered probe, so it is skipped, and a critical-skip is treated as failure → 'degraded'. This directly contradicts the user's standing requirement that the built-in doctor be a trustworthy self-monitor (a false-negative health report wastes turns and erodes trust).
Fix (pick the correct one):
  - PREFERRED: register a real probe for `hermes_contract_tool_registration` — it IS verifiable in-process (the `register()` wiring is already exercised by the `handler_exception_coverage` probe), so the probe can confirm the contract tools are registered and return pass; OR
  - If it genuinely cannot be probed in this environment, treat a critical check that is SKIPPED (not failed) as NOT degrading overall health — i.e. overall status is 'degraded' only on a real FAIL, while an unprobeable critical is reported as 'skip' and overall stays ok. 
Apply the same principle consistently: a skip is not a fail.
Invariant + test: on a healthy install, `cluxion-supercoder doctor` → overall ok=True, exit 0; a genuine failure still → degraded/exit 1. Add an environment-independent test (monkeypatch) mirroring the pattern already used elsewhere.

## Defect 2 (P3 — HONESTY): repo_map silently drops files beyond max_files cap
REPO_MAP_SCHEMA / docstring promises "files beyond the character budget are counted in files_omitted, never silently dropped." But when `scan_repo` hits the `max_files` cap, the extra files are dropped with `files_omitted=0` and `truncated=false`, so the host model receives a PARTIAL orientation map believing it is COMPLETE.
Fix: surface the file-count cap. When `scan_repo` hits `max_files`, either expose `files_scanned` vs a separate `files_capped` count, or set `truncated=True` and/or add the capped remainder to `files_omitted`, so the model knows the map is incomplete. Keep the existing character-budget omission accounting intact.
Invariant + test: a repo with more files than `max_files` → result has `truncated=True` or `files_omitted>0` reflecting the capped files (never `omitted=0`+`truncated=false` while files were actually dropped).

## Defect 3 (P3): is_coding_task naive substring match → false positives
`is_coding_task` uses raw substring matching, so non-coding prompts are misrouted into the coding queue: 'latest' matches 'test', 'prefix' matches 'fix', etc. Bias is fail-toward-coding so output isn't wrong, but it wastes a repo scan and emits a misleading 'coding task' classification + repo_map for clearly non-coding prompts.
Fix: use word-boundary matching (regex `\b<kw>\b` or tokenize-and-compare) instead of raw substring, so 'latest' does not match 'test' and 'prefix' does not match 'fix'. Keep the genuine-non-coding bypass branch.
Invariant + test: 'what is the latest news' → NOT classified coding; 'fix the bug in main.py' → classified coding. Add tests for the false-positive words ('latest', 'prefix', 'contest') and true positives ('fix', 'test', 'refactor', 'debug').

## Done criteria
- `uv run pytest` GREEN. `uv run ruff check .` pass.
- New tests for Defect 1 (healthy → ok/exit0, env-independent), Defect 2 (capped files surfaced), Defect 3 (word-boundary classification).
- No version bump in pyproject. No edits under `.grok-briefs/`. Provide a concise per-defect diff summary.
