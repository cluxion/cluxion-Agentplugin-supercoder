---
name: supercoder
description: Hash-verified patch discipline for coding tasks — bounded read windows, exact/fuzzy patch application that refuses ambiguous matches, automatic syntax gate with rollback, lint and test gates, and an evidence brief. Use when writing, refactoring, or fixing code across one or more files (구현, 리팩토링, 버그 수정, 패치, 코드 수정); especially multi-file, unfamiliar-codebase, or correctness-critical edits where stale writes and mis-applied patches must be caught. Skip for trivial single-line edits and non-code work.
---

# Cluxion Supercoder

Call the package CLI. It returns JSON contracts; the host agent owns file reads, edits, terminal commands, and final answers.

## Plan

```bash
cluxion-supercoder check
cluxion-supercoder plan --json-stdin
```

Minimum stdin:

```json
{"prompt":"<user request>","cwd":"<workspace>"}
```

If the result is `mode=bypass`, continue without Supercoder. If the result is `mode=coding_queue`, follow this workflow:

1. Use the plan and embedded `repo_map` for orientation; call `repo-map` when more map context is needed.
2. Call `read-window` before each edit and use the returned `file_hash`.
3. Call `patch` with exact `old_text`, `new_text`, and `expected_hash` (alias: `expected_file_hash`). Each successful patch is syntax-gated automatically — a patch that breaks parsing is rolled back — and lint findings ride along in `lint`.
4. After all patches land, call `syntax-gate`, `lint-gate`, and `test-gate` on the full `files_changed` list; the host must run any suggested tests in the terminal.
5. Call `brief` with `files_changed`, `tests_run`, `verification_status`, and remaining risks.

## JSON Commands

```bash
cluxion-supercoder read-window --json-stdin
cluxion-supercoder patch --json-stdin
cluxion-supercoder cursor-map --json-stdin
cluxion-supercoder repo-map --json-stdin
cluxion-supercoder syntax-gate --json-stdin
cluxion-supercoder lint-gate --json-stdin
cluxion-supercoder test-gate --json-stdin   # suggest-only: returns the test command to run, not per-file pass/fail (the HOST runs the tests)
cluxion-supercoder brief --json-stdin
```

Hashes in outputs are bare 64-char sha256 hex; hash inputs also accept an optional `sha256:` prefix.

Examples:

```bash
printf '{"cwd":"<workspace>","path":"src/app.py","start_line":1,"max_lines":40}' |
  cluxion-supercoder read-window --json-stdin
```

```json
{"ok":true,"path":"src/app.py","start_line":1,"end_line":12,"content":"...","content_hash":"<64-hex>","file_hash":"<64-hex>"}
```

`max_lines` is capped at 120 (`line_budget_exceeded:inspect` above that); read larger files in successive windows.

```bash
printf '{"cwd":"<workspace>","path":"src/app.py","old_text":"old\\n","new_text":"new\\n","expected_hash":"<64-hex>"}' |
  cluxion-supercoder patch --json-stdin
```

```json
{"ok":true,"file_path":"/workspace/src/app.py","strategy":"exact","message":"patch applied","expected_hash":"<64-hex>","matched_hash":"<64-hex>","similarity":1.0,"syntax":{"checked":true,"error_count":0,"language":"python","valid":true},"lint":{"clean":true,"finding_count":0,"tool":"ruff","truncated":false}}
```

`lint` appears only when a linter for the file's language is available.

```bash
printf '{"cwd":"<workspace>","files_changed":["src/app.py"]}' |
  cluxion-supercoder syntax-gate --json-stdin
```

```json
{"ok":true,"files":[{"path":"src/app.py","checked":true,"language":"python","valid":true,"error_count":0,"errors":[]}]}
```

```bash
printf '{"cwd":"<workspace>","files_changed":["src/app.py"]}' |
  cluxion-supercoder lint-gate --json-stdin
```

```json
{"ok":true,"files":[{"path":"src/app.py","checked":true,"language":"python","tool":"ruff","clean":true,"finding_count":0,"findings":[],"truncated":false}]}
```

```bash
printf '{"cwd":"<workspace>","files_changed":["src/app.py"]}' |
  cluxion-supercoder test-gate --json-stdin
```

```json
{"ok":true,"mode":"suggest_or_run","command":"pytest -q tests/test_app.py","targets":["tests/test_app.py"],"files_changed":["src/app.py"],"source":"mapped_from_files_changed"}
```

```bash
printf '{"files_changed":["src/app.py"],"tests_run":[{"command":"pytest -q tests/test_app.py","status":"passed"}],"verification_status":"passed","remaining_risks":[]}' |
  cluxion-supercoder brief --json-stdin
```

```json
{"ok":true,"brief":{"files_changed":["src/app.py"],"tests_run":[{"command":"pytest -q tests/test_app.py","status":"passed"}],"verification_status":"passed","remaining_risks":[]}}
```

## Failure paths

A failed `patch` returns `ok:false` with a `strategy` and a `retry` object
(`attempt`, `max_attempts`, `repeated_input`, `escalate`, `guidance`):

- `no_match` — `old_text` not found, or several fuzzy candidates scored too close to pick one (ambiguity refusal). Re-read the window and copy `old_text` exactly; widen it with surrounding lines to disambiguate.
- `stale_file` — the file changed after `read-window`; rebuild the cursor and use the fresh `file_hash`.
- `syntax_reverted` — the patch applied but broke parsing, so the file was restored to its pre-patch content; fix `new_text` using the returned `syntax_errors`.
- `missing_file` / `empty_old_text` — verify the path (`cursor-map`) / send non-empty `old_text`.

Follow `retry.guidance`; never resend a failed patch unchanged. When `escalate` is true the retry budget (3) is exhausted — stop patching and re-plan a smaller edit.

## Doctor

```bash
cluxion-supercoder check
cluxion-supercoder doctor
cluxion-supercoder doctor --json
```
