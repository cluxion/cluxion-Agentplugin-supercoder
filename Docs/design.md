# Design

## 목표

**연결된 AI**가 코딩 task에서도 **구조화된 하네스**를 따르도록 합니다. 모델 가중치를 바꾸지 않고:

- 읽기 범위 제한
- patch 좌표 고정
- evidence 기반 완료 판정

## Cursor logic

파일 전체가 아닌 **편집 좌표**를 제공합니다.

```text
LineWindow
  path, start_line, end_line
  content, content_hash, file_hash
```

규칙:

- 모든 read는 `max_lines` 이내
- patch 전 `expected_file_hash` 검증
- 파일 변경 시 stale → patch 거부

## Line budget

| 상황 | 기본 제한 |
|------|-----------|
| 위치 탐색 | 120 lines / file |
| patch context | ~100 lines |
| 리뷰 | 160 lines |
| 생성 파일 soft cap | 400 lines |

초과 시 `line_budget_exceeded` — split plan 또는 차단.

## Safe patch

hash/fuzzy match 로직:

1. exact match
2. fuzzy match (threshold 0.86, ambiguous 시 거부)
3. stale file hash → block

## Safety (fail-closed)

- workspace root 밖 path 차단
- destructive command 차단
- secret 경로 패턴 read/write 차단
- 400줄 초과 single write 차단

## Evidence contract

완료로 인정하려면 **연결된 AI**가 brief에 기록:

- 변경: `files_changed` 또는 `no_changes_needed`
- 테스트: `tests_run` 또는 `tests_not_run_reason`
- 불확실: `unknown_after_check` 허용, fake certainty 금지

## Repo map

`supercoder_repo_map`은 budgeted text map (files + top-level symbols with line numbers)을 반환합니다.
budget을 넘는 파일은 `files_omitted`에 집계됩니다. `supercoder_plan`은 coding plan에 기본 2000-char repo map을 embed하며 `repo_map:false`로 opt-out 가능합니다.

## Syntax and lint gates

- `supercoder_syntax_gate` — parse-check (stdlib: python/json/toml; tree-sitter: rust/js/ts/tsx); unsupported languages fail-open
- `supercoder_lint_gate` — advisory ruff (suggest-only, never blocks)
- `supercoder_patch` — 기본 `syntax_gate=true` (parse 실패 시 revert), `lint_gate=true` (advisory findings)

## Lazy activation

`is_coding_task()`가 false이면 `supercoder_plan`은 `mode=bypass`.