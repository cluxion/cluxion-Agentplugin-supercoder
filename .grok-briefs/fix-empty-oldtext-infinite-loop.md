# Task: supercoder hash_patch — fix infinite loop / OOM on empty old_text (_exact_spans empty-needle)

## Context (LIVE-VERIFIED crash — user-facing)
0.2.12 installed. `apply_patch(path, old_text="", new_text=...)` HANGS in an infinite loop and OOMs (process killed, exit 124 on timeout / 137 on OOM). Root cause in `core/hash_patch.py::_exact_spans`:
```python
while True:
    start = text.find(needle, offset)
    if start < 0:
        return spans
    spans.append((start, start + len(needle)))
    offset = start + len(needle)
```
With `needle == ""`, `text.find("", offset)` ALWAYS returns `offset` (never -1) and `offset += len("") == 0`, so `offset` never advances → infinite `spans.append` → unbounded memory → OOM. Reproduced live: `_exact_spans('hello', '')` times out (exit 124); `apply_patch(x, old_text='', new_text='INSERT')` times out. Any caller passing an empty `old_text` hangs the whole process. Do NOT bump the version. Do NOT touch `.grok-briefs/`.

## Fix
1. In `_exact_spans`: guard the empty needle at the top — `if not needle: return []` (an empty search string has no meaningful patch target).
2. In `apply_patch`: reject empty `old_text` early, BEFORE acquiring the lock / scanning, with a clear failure:
   `if not old_text: return _failed(str(path), "empty_old_text", expected_file_hash, "old_text must be non-empty")`.
3. Audit `_candidate_spans` / `_best_fuzzy_span` for the same empty-`reference` hazard: if `reference == ""` produces a degenerate or huge candidate set (or a misleading match), guard it too (e.g. early no_match / `if not reference: return []`). Verify with a timeout test that empty reference does not hang.

## Invariant + tests (MANDATORY)
- `apply_patch(..., old_text='')` returns a FAST failure (`success=False`, strategy/message indicating empty old_text), with NO hang — add a test asserting it returns in well under a second (e.g. wrap with a thread + timeout, or just assert it returns and check the strategy).
- `_exact_spans(text, '')` returns `[]` immediately.
- Non-empty old_text behavior is UNCHANGED (existing exact / fuzzy / ambiguity / concurrency tests stay green).
- `uv run pytest` GREEN; `uv run ruff check .` pass.

## Done
- Empty old_text no longer hangs/OOMs; it returns a fast, clear failure. Tests added proving no-hang. No version bump. No `.grok-briefs/` edits. Concise diff.
