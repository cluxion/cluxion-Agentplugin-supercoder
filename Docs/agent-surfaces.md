# Agent Surfaces

Supercoder는 Hermes plugin + `supercoder` toolset으로 연결됩니다.  
**연결된 AI**가 코딩 task에서 `supercoder_*` 도구를 호출합니다.

## Hermes

```bash
hermes plugins enable cluxion-agentplugin-supercoder
```

Tools (9): `supercoder_plan`, `supercoder_repo_map`, `supercoder_read_window`, `supercoder_patch`, `supercoder_cursor_map`, `supercoder_syntax_gate`, `supercoder_lint_gate`, `supercoder_test_gate`, `supercoder_brief`

## Claude / Codex / Grok

동일 tool semantics를 skill·CLI로 안내합니다.  
코딩 bounded read·patch·evidence 규칙은 Hermes와 같습니다.

## 연결된 AI 규칙

1. 코딩 요청만 `supercoder_plan` — 그 외 `bypass`
2. patch 전 `read_window`로 hash 확보
3. 테스트는 host terminal 실행 후 brief에 evidence 기록
4. stale·blocked 시 사용자에게 명시적 보고