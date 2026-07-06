---
name: cluxion-supercoder
description: Use Cluxion Supercoder for coding tasks that need bounded reads, hash-verified patches, syntax/lint/test gates, or evidence-based briefs.
disable-model-invocation: true
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
3. Call `patch` with exact `old_text`, `new_text`, and `expected_hash` (alias: `expected_file_hash`).
4. Call `syntax-gate`, `lint-gate`, and `test-gate`; the host must run any suggested tests in the terminal.
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

Examples:

```bash
printf '{"cwd":"<workspace>","path":"src/app.py","start_line":1,"max_lines":40}' |
  cluxion-supercoder read-window --json-stdin
```

```json
{"ok":true,"path":"src/app.py","start_line":1,"end_line":12,"content":"...","content_hash":"sha256:...","file_hash":"sha256:..."}
```

```bash
printf '{"cwd":"<workspace>","path":"src/app.py","old_text":"old\\n","new_text":"new\\n","expected_hash":"sha256:..."}' |
  cluxion-supercoder patch --json-stdin
```

```json
{"ok":true,"file_path":"/workspace/src/app.py","strategy":"exact","message":"patched","expected_hash":"sha256:...","matched_hash":"sha256:...","similarity":1.0}
```

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

## Doctor

```bash
cluxion-supercoder check
cluxion-supercoder doctor
cluxion-supercoder doctor --json
```
