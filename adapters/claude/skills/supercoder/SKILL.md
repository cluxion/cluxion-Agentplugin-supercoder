---
name: cluxion-supercoder
description: Coding harness for bounded reads, hash-verified patches, and evidence-based briefs. You (the connected AI) call supercoder tools before editing code.
---

# Supercoder — 연결된 AI 지시문

코딩 작업에서 **당신(연결된 AI)** 이 `supercoder_*` 도구를 호출합니다.

## 흐름

1. `supercoder_plan` — 코딩 task면 WorkUnit 큐, 아니면 `bypass`
2. `supercoder_cursor_map` / `supercoder_read_window` — bounded context + hashes
3. `supercoder_patch` — `expected_file_hash` 필수
4. host terminal에서 테스트 실행
5. `supercoder_brief` — `files_changed`, `tests_run` evidence 필수

## 규칙

- patch 전 반드시 read_window로 hash 확보
- stale hash면 patch 거부 — 다시 read
- 테스트 통과를 주장하려면 실제 실행 결과를 brief에 기록
- 비코딩 질문은 plan bypass로 오버헤드 최소화

## 설치 확인

```bash
cluxion-supercoder check
hermes plugins enable cluxion-agentplugin-supercoder
```