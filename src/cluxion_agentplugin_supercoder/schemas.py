from __future__ import annotations

PLAN_SCHEMA = {
    "name": "supercoder_plan",
    "description": (
        "Decompose a coding task into a WorkUnit queue. Coding plans also "
        "carry a compact repo map (files plus top-level symbols) for "
        "orientation; disable with repo_map:false."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string"},
            "task_id": {"type": "string"},
            "cwd": {"type": "string"},
            "repo_map": {"type": "boolean", "default": True},
            "repo_map_budget_chars": {"type": "integer", "minimum": 200, "default": 2000},
        },
        "required": ["prompt"],
    },
}

READ_WINDOW_SCHEMA = {
    "name": "supercoder_read_window",
    "description": "Read a bounded line window with hashes.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "start_line": {"type": "integer", "minimum": 1, "default": 1},
            "max_lines": {"type": "integer", "minimum": 1, "default": 120},
            "cwd": {"type": "string"},
        },
        "required": ["path"],
    },
}

PATCH_SCHEMA = {
    "name": "supercoder_patch",
    "description": "Apply hash-verified patch with stale cursor protection and an L1 syntax gate (auto-revert on parse failure).",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_text": {"type": "string"},
            "new_text": {"type": "string"},
            "expected_file_hash": {"type": "string"},
            "syntax_gate": {
                "type": "boolean",
                "default": True,
                "description": "Parse-check the patched file; revert the patch when it no longer parses.",
            },
            "lint_gate": {
                "type": "boolean",
                "default": True,
                "description": "After a successful patch, attach advisory ruff findings for the file (never blocks).",
            },
            "cwd": {"type": "string"},
        },
        "required": ["path", "old_text", "new_text"],
    },
}

SYNTAX_GATE_SCHEMA = {
    "name": "supercoder_syntax_gate",
    "description": "Parse-check a file or snippet (tree-sitter: python/rust/js/ts/tsx/json; stdlib: toml). Fail-open for unsupported languages.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File to check (relative to cwd)."},
            "files_changed": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Files to check and aggregate when path/content is not supplied.",
            },
            "content": {"type": "string", "description": "Snippet to check instead of a file."},
            "language": {
                "type": "string",
                "description": "Override language detection (python, rust, javascript, typescript, tsx, json, toml).",
            },
            "cwd": {"type": "string"},
        },
    },
}

LINT_GATE_SCHEMA = {
    "name": "supercoder_lint_gate",
    "description": "Advisory ruff lint for one file (suggest-only; respects the project's own ruff config).",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File to lint (relative to cwd)."},
            "files_changed": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Files to lint and aggregate when path is not supplied.",
            },
            "cwd": {"type": "string"},
        },
    },
}

CURSOR_MAP_SCHEMA = {
    "name": "supercoder_cursor_map",
    "description": "Build repo/file cursor index.",
    "parameters": {
        "type": "object",
        "properties": {"cwd": {"type": "string"}, "paths": {"type": "array", "items": {"type": "string"}}},
    },
}

TEST_GATE_SCHEMA = {
    "name": "supercoder_test_gate",
    "description": (
        "Suggest targeted test commands from changed files; host terminal runs them. "
        "Python files map to matching test files anywhere in the repo (src/, flat, or "
        "tests-beside-code layouts); .rs/.go/.js/.ts changes route to cargo/go/npm "
        "runners when the project marker exists."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "files_changed": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Paths edited in this task; mapped to the closest matching test files.",
            },
            "command": {"type": "string", "description": "Override suggested command (optional)."},
            "cwd": {"type": "string", "description": "Workspace root for test discovery."},
        },
    },
}

REPO_MAP_SCHEMA = {
    "name": "supercoder_repo_map",
    "description": (
        "Build a compact, budgeted repo map: files with line counts plus "
        "top-level symbols (classes, functions, methods, rust items, ts "
        "interfaces) with line numbers. Call once at task start to orient "
        "before reading files; files beyond the character budget are "
        "counted in files_omitted, never silently dropped."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "cwd": {"type": "string", "description": "Repo root to map."},
            "max_files": {"type": "integer", "minimum": 1, "default": 128},
            "max_symbols_per_file": {"type": "integer", "minimum": 1, "default": 24},
            "budget_chars": {
                "type": "integer",
                "minimum": 200,
                "default": 8000,
                "description": "Upper bound on the rendered map size.",
            },
        },
    },
}

BRIEF_SCHEMA = {
    "name": "supercoder_brief",
    "description": "Summarize changes, verification, and remaining risks.",
    "parameters": {"type": "object", "properties": {}},
}
