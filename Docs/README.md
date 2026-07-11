# Documentation

`clx-supercoder` 공개 문서입니다.

## 처음 읽는 분

**Supercoder**는 **코딩 작업**에서 큰 파일 추측·unsafe patch를 막는 **코딩 하네스 플러그인**입니다.

| 질문 | 답 |
|------|-----|
| **무엇을 하나요?** | line window 읽기, hash 검증 patch, line budget, WorkUnit 큐, safety gate를 제공합니다. |
| **누가 실행하나요?** | **연결된 AI**가 `supercoder_*` 도구를 호출하고, host terminal/file 도구와 함께 bounded 작업을 합니다. |
| **플러그인이 모델을 부르나요?** | **아니요.** plan·patch 검증·evidence 요구만 합니다. |
| **왜 Rust인가요?** | file hash·repo scan hot path를 `supercoder-index`가 담당합니다. |

### 연결된 AI 사용 흐름

1. 코딩 요청 → `supercoder_plan` (비코딩이면 `bypass`; coding plan에 repo map 포함, `repo_map:false`로 opt-out)
2. `supercoder_repo_map` / `supercoder_cursor_map` / `supercoder_read_window` — bounded context
3. `supercoder_patch` — expected hash 필수; `supercoder_syntax_gate` / `supercoder_lint_gate`로 검증
4. `supercoder_test_gate` — host terminal에서 테스트 실행
5. `supercoder_brief` — evidence와 함께 완료 보고

### 사람(개발자)이 할 일

```bash
pip install cluxion-agentplugin-supercoder
cluxion-supercoder check
hermes plugins enable cluxion-agentplugin-supercoder
```

## 목차

| 문서 | 내용 |
|------|------|
| [architecture.md](architecture.md) | 구조, host 경계, WorkUnit |
| [design.md](design.md) | cursor, patch, safety |
| [installation.md](installation.md) | 설치, Rust sidecar |
| [tools.md](tools.md) | toolset, WorkUnit, evidence |
| [agent-surfaces.md](agent-surfaces.md) | Hermes / Claude / Codex 연동 |
| [capabilities.md](capabilities.md) | 현재 제공 기능 |
| [rust-architecture.md](rust-architecture.md) | Rust 메인 · Python adapter |

## 이 레포에서 다루지 않는 것

- API 키, OAuth, provider 비밀
- 플러그인 내부 LLM 호출
- 비공개 운영 정보

이슈는 GitHub Issues를 이용해 주세요.