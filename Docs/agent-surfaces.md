# Agent Surfaces

Supercoder uses one root plugin artifact for Codex and Claude Code, plus the existing Hermes
entry point. Every surface calls the same `cluxion-supercoder` JSON contracts.

## Codex

Codex uses the root marketplace artifact:

```bash
codex plugin marketplace add cluxion-local /path/to/cluxion-Agentplugin-supercoder
codex plugin add cluxion-agentplugin-supercoder@cluxion-local
```

Files:

- `.codex-plugin/plugin.json`
- `commands/`
- `skills/supercoder/SKILL.md`

No `[plugins.<name>] command = [...]` schema exists.

## Claude Code

Claude Code uses the same root layout:

- `.claude-plugin/plugin.json`
- `commands/`
- `skills/supercoder/SKILL.md`

## Hermes

```bash
hermes plugins enable cluxion-agentplugin-supercoder
```

Hermes registers the `supercoder` toolset through `hermes_agent.plugins`.

Tools (10): `supercoder_plan`, `supercoder_repo_map`, `supercoder_read_window`, `supercoder_patch`,
`supercoder_cursor_map`, `supercoder_syntax_gate`, `supercoder_lint_gate`, `supercoder_test_gate`,
`supercoder_brief`, `supercoder_doctor`

## Host Agent Rules

1. Run `cluxion-supercoder plan --json-stdin` only for coding requests; respect `mode=bypass`.
2. Run `read-window` before each patch and use the returned `file_hash`.
3. Run suggested tests in the host terminal and record evidence in `brief`.
4. Report stale cursors, retry exhaustion, and blocked checks honestly.
