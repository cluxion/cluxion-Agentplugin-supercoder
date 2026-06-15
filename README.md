========= Written in Korean first, then English ==========

======== 한국어 ========

# cluxion-agentplugin-supercoder

AI 에이전트(Hermes Agent, Claude Code, Codex)를 위한 코딩 플러그인입니다. 모델이 — 작은 로컬
모델이라도 — 코드를 안전하고 정확하게 편집하도록 돕습니다. 모든 패치는 편집 대상 파일과 대조해
검증되고, 깨진 구문은 피드백과 함께 자동으로 되돌려지며, 에이전트는 시작 전에 레포의 간결한 지도를
받습니다.

## 설치

```bash
pip install cluxion-agentplugin-supercoder
```

### Hermes Agent에서 사용

`~/.hermes/config.yaml` 에 추가한 뒤 Hermes를 재시작하세요.

```yaml
plugins:
  enabled:
    - cluxion-agentplugin-supercoder
```

Hermes를 통해 제공되는 로컬 모델(vLLM/MLX)에서도 동일하게 동작합니다.

## 기능

코딩 중에 에이전트가 `supercoder_*` 도구 세트를 자동으로 사용합니다.

- **안전한 편집** — 패치를 파일 내용과 대조해 검증하므로, 모델이 잘못된 위치나 오래된 버전을 편집할
  수 없습니다.
- **구문 / 린트 / 테스트 게이트** — 깨진 편집은 자동으로 되돌려지고 무엇이 잘못됐는지 모델에 알려주므로,
  깨진 코드를 남기지 않고 다시 시도합니다.
- **레포 지도(repo map)** — 코딩 계획에 파일과 최상위 함수·클래스의 예산 내 개요가 함께 제공되어,
  모델이 경로를 추측하지 않습니다.

## 점검

설치·Hermes 계약·네이티브 백엔드 상태를 결정론적으로 자가 진단합니다. 같은 상태면 항상 같은 결과를
출력하고, 문제가 있으면 증상과 해결 단계를 그대로 알려줍니다.

```bash
cluxion-supercoder doctor          # 사람용 요약
cluxion-supercoder doctor --json   # 구조화 출력
```

Hermes 안에서는 `supercoder_doctor` 도구로도 노출됩니다.

## 라이선스

Apache-2.0

============ English ==========

# cluxion-agentplugin-supercoder

A coding plugin for AI agents (Hermes Agent, Claude Code, Codex). It helps the model — even
a smaller local one — edit code safely and accurately: every patch is checked against the
file it's editing, broken syntax is auto-reverted with feedback, and the agent gets a compact
map of your repo before it starts.

## Install

```bash
pip install cluxion-agentplugin-supercoder
```

### Use with Hermes Agent

Add it to `~/.hermes/config.yaml`, then restart Hermes:

```yaml
plugins:
  enabled:
    - cluxion-agentplugin-supercoder
```

It works the same with local models (vLLM/MLX) served through Hermes.

## What you get

While coding, your agent uses a `supercoder_*` toolset automatically:

- **Safe edits** — patches are verified against the file's content, so the model can't edit
  the wrong place or a stale version.
- **Syntax / lint / test gates** — a broken edit is automatically reverted and the model is
  told what went wrong, so it retries instead of leaving broken code behind.
- **Repo map** — coding plans come with a budgeted overview of your files and their top-level
  functions and classes, so the model stops guessing paths.

## Diagnostics

A deterministic self-check of install, the Hermes contract, and the native backend. The same
state always prints the same result, and on any problem it shows the symptom and the exact fix
steps.

```bash
cluxion-supercoder doctor          # human summary
cluxion-supercoder doctor --json   # structured output
```

Also exposed inside Hermes as the `supercoder_doctor` tool.

## License

Apache-2.0
