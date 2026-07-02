---
description: Run Cluxion Supercoder planning for a coding task.
argument-hint: "<task>"
---

Run:

```bash
cluxion-supercoder plan --json-stdin
```

stdin:

```json
{"prompt":"$ARGUMENTS","cwd":"$PWD"}
```

Use the JSON contract to decide whether the task is a coding task. If `mode` is `coding_queue`, follow the Supercoder workflow from the `supercoder` skill before editing.
