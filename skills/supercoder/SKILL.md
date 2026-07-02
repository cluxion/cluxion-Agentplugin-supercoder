---
name: cluxion-supercoder
description: Use Cluxion Supercoder for coding tasks that need bounded reads, hash-verified patches, syntax/lint/test gates, or evidence-based briefs.
---

# Cluxion Supercoder

Call the package CLI. It returns JSON contracts; the host agent owns file reads, edits, terminal commands, and final answers.

## Plan

```bash
cluxion-supercoder plan --json-stdin
```

Minimum stdin:

```json
{"prompt":"<user request>","cwd":"<workspace>"}
```

If the result is `mode=bypass`, continue without Supercoder. If the result is `mode=coding_queue`, follow this workflow:

1. Use the plan and embedded `repo_map` for orientation; call `repo-map` when more map context is needed.
2. Call `read-window` before each edit and use the returned `file_hash`.
3. Call `patch` with exact `old_text`, `new_text`, and `expected_file_hash`.
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
cluxion-supercoder test-gate --json-stdin
cluxion-supercoder brief --json-stdin
```

## Doctor

```bash
cluxion-supercoder doctor
cluxion-supercoder doctor --json
```
