# Hermes adapter

```bash
pip install cluxion-agentplugin-supercoder
hermes plugins enable cluxion-agentplugin-supercoder
cluxion-supercoder check
```

Tools: `supercoder_plan`, `supercoder_read_window`, `supercoder_patch`, `supercoder_cursor_map`, `supercoder_test_gate`, `supercoder_brief`

연결된 AI가 코딩 task에서 위 도구를 순서대로 호출합니다.

## 슬래시 (0.2.15+)

```
/supercoder <task>        # 코딩 모드 — plan + 패치/게이트 워크플로우 지시
/supercoder-doctor
```

`/` 입력 시 `/super`로 필터 · `/supercoder`는 에이전트 턴으로 전달(`deliver=agent`).