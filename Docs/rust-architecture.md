# Rust Architecture

## 원칙

Supercoder는 **Rust가 hot path**, **Python이 에이전트 연결**입니다.

```
Hermes / Claude / Codex / Grok
        ↓
cluxion_agentplugin_supercoder  (Python: plugin, runner, safety)
        ↓
supercoder-index                (Rust: hash, scan)
        ↓ fallback
hash_patch / cursor             (Python: 동일 계약)
```

## Rust: `supercoder-index`

| 명령 | 역할 |
|------|------|
| `hash` | 파일 SHA-256 (빠른 경로) |
| `scan` | bounded repo walk + line count |

```bash
cargo build --release --manifest-path rust/supercoder_index/Cargo.toml
export CLUXION_SUPERCODER_INDEX_BIN=/path/to/supercoder-index
```

## Python 역할

- `hash_patch`, `cursor`, `repo_map`, `syntax_gate`, `lint_gate`, `line_budget`, `safety` — 에이전트 tool handler
- `TaskQueue` / WorkUnit 분해
- Hermes `register()` — `supercoder` toolset 등록
- 연결된 AI가 skill·도구 지시에 따라 `supercoder_*` 호출

## 범용성

`plugin.yaml`의 `surfaces` 목록과 동일 core를 모든 agent에 연결합니다.  
환경마다 **등록 방식만 다르고** patch·cursor 로직은 공유합니다.