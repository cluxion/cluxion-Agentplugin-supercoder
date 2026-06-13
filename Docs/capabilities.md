# Capabilities (v0.1.0)

Supercoder가 **현재 제공하는** 기능입니다.

## Hermes 연동

- Plugin entry + `supercoder` toolset
- Tools (9): `supercoder_plan`, `supercoder_repo_map`, `supercoder_read_window`, `supercoder_patch`, `supercoder_cursor_map`, `supercoder_syntax_gate`, `supercoder_lint_gate`, `supercoder_test_gate`, `supercoder_brief`

## Core (Python)

- `hash_patch` — exact/fuzzy match, stale detection
- `cursor` — read_window, cursor_map
- `repo_map` — budgeted files + top-level symbols (`files_omitted` honesty)
- `syntax_gate`, `lint_gate` — parse-check and advisory ruff
- `line_budget`, `safety` gates
- `TaskQueue` / `plan_coding_task` — WorkUnit 분해 (plan embeds 2000-char repo map by default)

## Rust sidecar

- `supercoder-index hash` — 파일 SHA-256
- `supercoder-index scan` — bounded repo walk
- 미설치 시 Python fallback

## 연결된 AI 책임

- 도구 호출 순서·evidence 수집·테스트 실행 (host terminal)
- `supercoder_brief`에 `files_changed`, `tests_run` 기록
- patch 실패·blocked 시 사용자에게 명시적 보고

## 의도적으로 포함하지 않음

- Provider/OAuth/모델 선택 (host 소유)
- 전처리·일반 큐 ([preprocessing](https://github.com/cluxion/cluxion-Agentplugin-preprocessing) 플러그인)