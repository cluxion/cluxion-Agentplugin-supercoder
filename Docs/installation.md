# Installation

## 요구 사항

- Python 3.11+
- (선택) Rust — `supercoder-index` 빌드

## 설치

```bash
pip install cluxion-agentplugin-supercoder
cluxion-supercoder check
```

개발:

```bash
uv venv && uv pip install -e ".[dev]"
uv run pytest
uv run ruff check .
```

## Hermes Agent

```bash
hermes plugins enable cluxion-agentplugin-supercoder
```

toolset: `supercoder`

레거시 entry point 이름 `hermes-supercoder`도 호환용으로 제공됩니다.

## Rust sidecar (선택)

```bash
cargo build --release --manifest-path rust/supercoder_index/Cargo.toml
export CLUXION_SUPERCODER_INDEX_BIN=/path/to/supercoder-index
```

`cluxion-supercoder check`에서 `rust_index: true` 확인.

## 다른 에이전트

Claude / Codex / Grok: `plugin.yaml`의 `surfaces` 목록 참고.  
각 환경의 공식 plugin/skill 메커니즘으로 `register()` 또는 skill 문서를 연결합니다.