# Task: supercoder hash_patch — fix concurrent lost-update race (flock on a replaced inode)

## Context
A live stress run found `tests/test_hash_patch.py::test_concurrent_patches_no_lost_update` is FLAKY (~1 in 12-15 runs): 8 concurrent ThreadPoolExecutor workers each apply a distinct, non-overlapping patch to the same file. Occasionally one marker stays `# UNIQUE_PATCH_i_START` (a lost update) even though every worker returns `success=True`. This is a real concurrency bug: two agents/threads patching the same file can silently lose one patch (user-facing code loss). Do NOT bump the version (deploy handled separately). Do NOT touch `.grok-briefs/`.

## Root cause (verified by reading `core/hash_patch.py`)
`_exclusive_lock(path)` does `os.open(path)` then `fcntl.flock(fd, LOCK_EX)`. But `_commit` → `_atomic_write` finalizes via `os.replace(tmp, path)`, which swaps `path` to a NEW inode. The flock is held on the OLD inode, so the lock does not actually serialize writers across the replace. Sequence that loses a patch:
1. Thread A: `os.open(path)` → inode1; acquires flock(inode1).
2. Thread B: `os.open(path)` → inode1 (before A's replace); blocks on flock(inode1).
3. A: reads inode1, patches its marker, `_atomic_write` → `os.replace` → `path` now = inode2; A releases flock(inode1).
4. B: acquires flock(inode1), but reads **inode1** (STALE — A's change is in inode2, not inode1), patches only its own marker, `os.replace` → `path` = inode3. **A's patch is lost.**
Because the lock lives on a file that is replaced out from under it, two writers can serialize on DIFFERENT inodes and lose updates. Additional window: when `path` does not exist, `_exclusive_lock` yields WITHOUT any lock (`if fcntl is None or not path.exists(): yield; return`), so concurrent creation is unserialized too.

## Fix
Lock on a STABLE sidecar lock file whose inode never changes, instead of on the target file that gets replaced:
- Choose a fixed lock path next to the target, e.g. `lock_path = path.parent / f".{path.name}.cluxion-lock"`.
- `_exclusive_lock` opens the lock file with `os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)`, `fcntl.flock(fd, fcntl.LOCK_EX)`, yields, then `fcntl.flock(fd, fcntl.LOCK_UN)` and `os.close(fd)` in `finally`.
- Do NOT delete the lock file (deleting it reintroduces an inode/unlink race); leaving an empty `.cluxion-lock` is harmless and gets reused.
- The lock file inode is never `os.replace`d, so all threads AND processes serialize correctly regardless of the target's atomic replace.
- Acquire the lock even when `path` does not yet exist (the lock file is always creatable), so creation is serialized too. Keep the actual "file not found" handling in `apply_patch` as-is (it returns missing_file), but acquire/serialize first if you choose to create files later — at minimum, the existing-file patch path must serialize.
- Graceful degrade when `fcntl is None` (non-POSIX, e.g. Windows): fall back to a module-level `threading.Lock()` so at least same-process thread safety holds (the test is single-process multithread, so this fallback alone fixes the test on platforms without fcntl).
- Read-modify-write (`path.read_text` → match → `_atomic_write`) must stay entirely INSIDE the lock (it already is — keep it).

## Invariant + tests
- Make `test_concurrent_patches_no_lost_update` pass DETERMINISTICALLY. Add a stress test that runs the 8-worker scenario in a loop (e.g. 50 iterations) and asserts ZERO lost updates across all iterations (all 8 markers end `_DONE`, none `_START`, every worker success).
- No regression: existing exact / fuzzy / stale-hash / `test_atomic_write_interruption_leaves_original_intact` tests still pass.
- `uv run pytest` GREEN, `uv run ruff check .` pass.

## Done criteria
- Race fixed at the lock layer (sidecar lock file + threading.Lock fallback). Deterministic stress test added. No version bump. No `.grok-briefs/` edits. Concise diff summary.
