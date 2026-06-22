# Task: Complete supercoder doctor coverage — register the missing critical security probes

## Context
The doctor catalog declares 31 checks but probes.py registers only 12. The probes that would
catch security holes are UNREGISTERED and silently skip — which is exactly how the two P0
read-path holes (now FIXED: read_window secret leak + sibling-dir escape) reached a shipped
build. Make doctor actually verify them so a regression can never silently ship again.

## Implement (in doctor/probes.py — follow the EXISTING probe registration pattern)
Register these catalog checks as real probes (the catalog's `detection_method` describes each test):
1. `path_security_secrets_blocked` (critical) — assert BOTH `read_window_tool` AND `patch_tool` on a
   `.env` (and another secret name like `credentials`) return ok=False with the secret-blocked
   message. This is the regression guard for P0-1.
2. `hermes_context_workspace_root` (critical) — assert `read_window` on `../../../etc/passwd` AND on
   a sibling-prefix escape (workspace `/work` + path `../work2/x`) are blocked. Regression guard for
   P0-2. (Both fixes already live in core/cursor.py + runner.py via pre_tool_gate + is_relative_to.)
3. `patch_cursor_validity`, `stale_cursor_protection_enforced` (critical) — implement per their
   catalog detection_method if feasible standalone. If a probe genuinely cannot run standalone,
   make the doctor SUMMARY downgrade to "degraded" whenever a CRITICAL catalog check is unregistered
   — never report green while a critical check silently skips.
4. Fix `handler_exception_coverage` (probes.py:111,114): it imports a nonexistent `_json_result`;
   the real symbol is `_wrap` (plugin.py:117). Point the probe at `_wrap` (wrap a raising callback,
   assert it degrades to error-JSON), or delete the dead probe.

## Invariants (MUST hold)
- Probes are READ-ONLY and use temp dirs; never mutate the real workspace or touch real secrets.
- Existing passing checks stay green; NO behavior change to runtime tools or the read-path fix.

## Tests (must pass; prove the probes have teeth)
- `uv run pytest` green. The new security probes PASS now (P0s fixed).
- Add a focused test proving each security probe DETECTS an ungated read (e.g. monkeypatch the gate
  to a no-op and assert the probe reports fail) — so the probe can't silently pass when broken.

## Out of scope
- No version bump / build / publish. No change to the read-path security fix (already correct).

## Done
doctor registers + passes path_security_secrets_blocked and hermes_context_workspace_root (with
teeth-proving tests), handler_exception_coverage points at _wrap, summary never reports green while
a critical check silently skips, all tests green. Concise diff summary.
