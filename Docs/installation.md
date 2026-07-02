# Installation

## Requirements

- Python 3.11+
- Optional Rust toolchain for `supercoder-index`

## Python package

```bash
pip install cluxion-agentplugin-supercoder
cluxion-supercoder check
```

Development:

```bash
uv venv && uv pip install -e ".[dev]"
uv run pytest
uv run ruff check .
```

## Codex marketplace install

Local path example:

```bash
codex plugin marketplace add cluxion-local /path/to/cluxion-Agentplugin-supercoder
codex plugin add cluxion-agentplugin-supercoder@cluxion-local
```

Git URL example:

```bash
codex plugin marketplace add cluxion https://github.com/cluxion/cluxion-Agentplugin-supercoder
codex plugin add cluxion-agentplugin-supercoder@cluxion
```

Do not use a `[plugins.<name>] command = [...]` block; Codex plugins are marketplace plugins.

## Claude Code plugin install

Install the same repository from the root `.claude-plugin/plugin.json`, then use the `supercoder`
skill or `/supercoder` and `/supercoder-doctor` commands. The commands call `cluxion-supercoder`
and leave model execution to the host agent.

## Hermes Agent

```bash
hermes plugins enable cluxion-agentplugin-supercoder
```

toolset: `supercoder`

Hermes support stays on the existing `hermes_agent.plugins` entry point. Slash commands are registered
by the plugin at session start.

## Rust sidecar (optional)

```bash
cargo build --release --manifest-path rust/supercoder_index/Cargo.toml
export CLUXION_SUPERCODER_INDEX_BIN=/path/to/supercoder-index
```

`cluxion-supercoder check` reports `rust_index: true` when the sidecar is available.
