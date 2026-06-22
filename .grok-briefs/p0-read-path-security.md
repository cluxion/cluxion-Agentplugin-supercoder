# Task: Fix 2 P0 security holes in supercoder's read path (live-verified)

## Context (both confirmed by live execution)
1. **Secret-file read leak** — `read_window_tool` (src/cluxion_agentplugin_supercoder/runner.py:73-92)
   and `cursor.read_window` (src/cluxion_agentplugin_supercoder/core/cursor.py:30-35) BYPASS the
   security gate. `patch_tool` calls `pre_tool_gate` (runner.py:98) and refuses secret files, but the
   read path never does. Live: `read_window_tool({'cwd':'/tmp','path':'.env'})` returned ok=True and
   leaked `AWS_SECRET=...`; also leaks `credentials`, `sub/.env`. `supercoder_read_window` is a
   REGISTERED host tool (plugin.py:25-31), so any agent can exfiltrate `.env`/`id_rsa`/`credentials`/
   secrets. `tests/test_safety.py::test_secret_paths_blocked` proves the intent for the patch path.

2. **Workspace sibling-directory escape** — `cursor.read_window` (core/cursor.py:33) uses
   `str(path).startswith(str(root.resolve()))`. This is string-prefix, not containment. Live:
   `read_window(Path('/tmp/work'), '../work2/secret.py')` leaked because `/tmp/work2` string-prefixes
   `/tmp/work`. Plain `../` is caught, but a sibling dir sharing the name prefix escapes.
   `safety._path_gate` (safety.py:~54) already does this correctly with `is_relative_to`, and
   `tests/test_safety.py::test_sibling_directory_prefix_is_not_containment` regression-guards the
   patch path.

## Fix (mirror the patch path's existing, correct gate — do NOT invent a new policy)
1. Route BOTH `cursor.read_window` and `read_window_tool` through the SAME secret-blocking +
   containment gate the patch path uses (`pre_tool_gate` / `_path_gate` / `_SECRET_PARTS` in
   safety.py and runner.py:98). Reading `.env`/`credentials`/`secrets`/`id_rsa` (the same
   `_SECRET_PARTS` set patch uses) must return `ok=False` with the SAME message/shape as
   `patch_tool` ("secret file access blocked").
2. core/cursor.py:33 — replace the `startswith` prefix check with
   `if not path.is_relative_to(root.resolve()): <reject>`. This closes BOTH plain `../` traversal
   and the sibling-prefix escape.
3. Legitimate in-workspace, non-secret reads MUST keep working unchanged (same return shape).

## Reference
- `safety.py`: `_path_gate` (~line 54, uses `is_relative_to`), `pre_tool_gate`, `_SECRET_PARTS`.
- `runner.py:98`: `patch_tool` calls `pre_tool_gate` — mirror exactly for the read path.

## Tests (MUST pass; add the missing coverage — tests/test_cursor.py currently has ZERO escape/secret tests)
- read_window / read_window_tool on `.env` and `credentials` → blocked (ok=False, secret message).
- read_window with a sibling-prefix escape (`/tmp/work` + `../work2/x`) → blocked.
- read_window with plain `../../etc/passwd` → blocked.
- read_window on a normal in-workspace file → STILL returns content (ok=True).
- All existing `tests/test_safety.py` and other tests stay green. `uv run pytest` green.

## Out of scope (DO NOT)
- No version bump, build, wheel, pip install, or publish.
- No change to patch-path semantics (already correct) or to the native backend.
- Optional ONLY if zero-risk: doctor probe `_json_result`→`_wrap` misname (probes.py:111,114). The
  two security fixes are the priority — never let probe cleanup endanger them.

## Done
read_window (both entry points) is gated identically to patch (secret-block + is_relative_to
containment); new tests prove both P0s are closed and normal reads still work; all tests green.
Report a concise diff summary and confirm the live exploits no longer reproduce.
