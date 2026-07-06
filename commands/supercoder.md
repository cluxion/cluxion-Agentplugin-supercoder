---
description: Run Cluxion Supercoder planning for a coding task.
argument-hint: "<task>"
---

Run:

```bash
cluxion-supercoder check
cluxion-supercoder plan --json-stdin
```

stdin:

```json
{"prompt":"$ARGUMENTS","cwd":"$PWD"}
```

Use the JSON contract to decide whether the task is a coding task. If `mode` is `coding_queue`, read `${CLAUDE_PLUGIN_ROOT}/skills/supercoder/SKILL.md` and follow its Supercoder workflow before editing.

Other JSON contracts:

```bash
printf '{"cwd":"'$PWD'","path":"src/app.py","start_line":1,"max_lines":40}' |
  cluxion-supercoder read-window --json-stdin
```

```json
{"ok":true,"path":"src/app.py","start_line":1,"end_line":12,"content":"...","content_hash":"sha256:...","file_hash":"sha256:..."}
```

```bash
printf '{"cwd":"'$PWD'","path":"src/app.py","old_text":"old\\n","new_text":"new\\n","expected_hash":"sha256:..."}' |
  cluxion-supercoder patch --json-stdin
```

```json
{"ok":true,"file_path":"/workspace/src/app.py","strategy":"exact","message":"patched","expected_hash":"sha256:...","matched_hash":"sha256:...","similarity":1.0}
```

```bash
printf '{"cwd":"'$PWD'","files_changed":["src/app.py"]}' |
  cluxion-supercoder syntax-gate --json-stdin
```

```json
{"ok":true,"files":[{"path":"src/app.py","checked":true,"language":"python","valid":true,"error_count":0,"errors":[]}]}
```

```bash
printf '{"cwd":"'$PWD'","files_changed":["src/app.py"]}' |
  cluxion-supercoder lint-gate --json-stdin
```

```json
{"ok":true,"files":[{"path":"src/app.py","checked":true,"language":"python","tool":"ruff","clean":true,"finding_count":0,"findings":[],"truncated":false}]}
```

```bash
printf '{"cwd":"'$PWD'","files_changed":["src/app.py"]}' |
  cluxion-supercoder test-gate --json-stdin   # suggest-only: returns the test command to run, not per-file pass/fail (the HOST runs the tests)
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
