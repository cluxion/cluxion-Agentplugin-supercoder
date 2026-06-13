# Tools

연결된 AI가 호출하는 `supercoder` toolset입니다.

## `supercoder_plan`

코딩 task를 WorkUnit 큐로 분해합니다. Coding plan에는 compact repo map (files + top-level symbols)이 포함됩니다.

- 비코딩 prompt → `{ "mode": "bypass" }`
- 코딩 prompt → `map` → `edit` → `verify` → `brief`

입력: `prompt`, `task_id`, `cwd`, `repo_map` (기본 `true`), `repo_map_budget_chars` (기본 2000)

## `supercoder_repo_map`

budgeted repo map: line count가 있는 파일 목록 + top-level symbols (class/function/method/rust item/ts interface)와 line number.
task 시작 시 orientation용. budget 초과 파일은 `files_omitted`에 집계 (silent drop 없음).

입력: `cwd`, `max_files` (기본 128), `max_symbols_per_file` (기본 24), `budget_chars` (기본 8000)

## `supercoder_read_window`

bounded file read.

입력: `path`, `start_line`, `max_lines` (기본 120), `cwd`  
출력: `content`, `content_hash`, `file_hash`

## `supercoder_patch`

hash 검증 patch. `expected_file_hash` 없으면 stale로 거부.
기본 `syntax_gate=true`: parse 실패 시 patch 자동 revert. `lint_gate=true`: 성공 후 advisory ruff findings 첨부 (never blocks).

## `supercoder_syntax_gate`

파일 또는 snippet parse-check (tree-sitter: python/rust/js/ts/tsx/json; stdlib: toml). 미지원 언어는 fail-open.

입력: `path`, `content`, `language`, `cwd`

## `supercoder_lint_gate`

advisory ruff lint for one file (suggest-only; 프로젝트 ruff config 존중).

입력: `path`, `cwd`

## `supercoder_cursor_map`

repo 내 소스 파일 index (확장자·깊이 제한).

## `supercoder_test_gate`

변경 파일 기반으로 테스트 명령을 **제안**합니다.

- Python: `src/pkg/store.py` → matching test file anywhere in repo (`src/`, flat, tests-beside-code)
- `.rs`/`.go`/`.js`/`.ts`: cargo/go/npm runner when project marker exists
- `command`로 명시 override 가능
- **연결된 AI**가 host terminal에서 실행하고, stdout/stderr를 `supercoder_brief.tests_run`에 기록

## `supercoder_brief`

`files_changed`, `tests_run`, `verification_status`, `remaining_risks` 요약.

## WorkUnit 필드

```text
id, goal, priority
allowed_paths, line_budget
dependencies, expected_evidence
status: pending | running | complete | failed | blocked
```

## Queue (내부)

`TaskQueue.next_unit()` / `record()` — `supercoder_plan` 흐름에서 사용.  
연결된 AI는 WorkUnit 순서에 맞게 위 도구를 호출합니다.