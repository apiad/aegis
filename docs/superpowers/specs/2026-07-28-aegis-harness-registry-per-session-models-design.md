# Harness registry + per-session model & effort selection

- **Status:** approved (design)
- **Date:** 2026-07-28
- **Repo:** `repos/aegis`
- **Author:** Claude (aegis session `harness-registry-models`) + Alex

## Problem

Today an aegis `Agent` is a monolith: `(harness, model, effort, permission)`
baked into `.aegis.yaml`. Three consequences:

1. **Changing a model means editing YAML.** There is no way to pick a model
   (or effort) when you spawn a tab by hand.
2. **Credentials/endpoints are embedded per-agent.** `base_url` / `api_key_file`
   live on the Lovelaice `Provider` object, so the same endpoint re-declared
   across profiles is duplicated, and the same driver pointed at two endpoints
   (e.g. OpenRouter vs a local Ollama) can't be expressed cleanly.
3. **OpenCode is second-class.** `opencode acp` takes no `-m` flag, so
   `OpenCodeDriver` runs `opencode acp` verbatim and OpenCode silently falls
   back to its own config default — `agent.model` is ignored. OpenCode's free
   models (`opencode/deepseek-v4-flash-free`, `laguna-s-2.1-free`,
   `ling-3.0-flash-free`, `mimo-v2.5-free`, `nemotron-3-ultra-free`,
   `north-mini-code-free`) are unreachable through aegis.

## Goals

- **Make OpenCode first-class**: `agent.model` actually selects the OpenCode
  model, including the free tier.
- **Register harnesses (providers) once** in `.aegis.yaml` as a named registry
  carrying driver + credentials/endpoint.
- **Choose model + effort per session** at interactive spawn, on top of the
  named presets — *both* named profiles (for the coordination substrate) and
  a live per-session picker.

## Non-goals

- **Mid-session model switching** (a `/model` swap on a live tab). For ACP /
  native drivers this means respawning the subprocess and resuming; it is a
  meaningfully larger chunk and is deferred to a follow-up.
- Reworking queues/schedules/groups. They keep referencing **named agent
  profiles** only. No change to their config surface.
- A universal pricing catalog for every provider. Pricing stays in
  `models.yaml` where it exists; unknown/free models report 0/unknown.

## Design

Split today's monolithic `Agent` into two layers:

1. **Harness registry** (top-level `harnesses:`) — the "provider": which driver,
   plus credentials/endpoint. Registered once.
2. **Per-session choice** of `model + effort` at interactive spawn, referencing
   a registered harness. Named `agents:` presets remain and seed the picker's
   fast path.

### 1. Config model

#### `HarnessRegistration`

New frozen dataclass, loaded from a top-level `harnesses:` section with
`.aegis/harnesses/*.yaml` overlays (mirroring `agents` / `queues` / `groups`):

```yaml
harnesses:
  claude:                       # registry key (the "harness name")
    driver: claude-code
  opencode:                     # OpenCode Zen, uses OpenCode's own auth
    driver: opencode
  openrouter:                   # direct-API via the native lovelaice agent
    driver: lovelaice
    base_url: https://openrouter.ai/api/v1
    api_key_file: ~/Workspace/.claude/openrouter.token
  local:                        # a SECOND lovelaice endpoint
    driver: lovelaice
    base_url: http://charizard.local:11434/v1
```

Fields:

| field | type | notes |
|-------|------|-------|
| `name` | str | the registry key |
| `driver` | `claude-code` \| `gemini` \| `opencode` \| `lovelaice` | the low-level driver |
| `base_url` | str? | endpoint (lovelaice/direct-API) |
| `api_key_file` | str? | path to key file (expanded, read at spawn) |
| `default_model` | str? | fallback when an agent/session omits `model` |
| `permission_default` | Permission? | default permission for sessions on this harness |

**Implicit registrations.** The four driver strings auto-register as implicit
harnesses named after themselves (`claude-code`, `gemini`, `opencode`,
`lovelaice`) with no credentials. This is what keeps every existing config
working with zero edits. An **explicit** registration of the same name wins
over the implicit one.

#### `Agent` — harness-ref path

```yaml
agents:
  opus:      { harness: claude,     model: opus,   effort: high }
  fast-free: { harness: opencode,   model: opencode/deepseek-v4-flash-free }
  qwen:      { harness: openrouter, model: qwen/qwen3-32b }
```

Resolution: `agent.harness → HarnessRegistration → driver + credentials`, then
overlay `model / effort / permission` from the agent. When `model` is omitted,
fall back to the harness's `default_model`.

**Back-compat — all three legacy shapes still validate:**

- Flat driver string: `Agent(harness="claude-code", model="opus", effort=...)`.
  `harness="claude-code"` resolves to the implicit `claude-code` harness.
- Provider object: `Agent(provider=ClaudeCode(model="opus", effort="high"))`.
  The `_sync_provider_and_flat` validator keeps mapping it to flat fields;
  those then resolve to the implicit harness. Lovelaice's embedded
  `base_url`/`api_key_file` remain a **fallback** read when the resolved
  harness registration doesn't supply them.
- New harness-ref: `harness=<explicit registry key>`.

Credential resolution order at spawn: **harness registration first**, then the
Lovelaice `Provider` object fields (back-compat), then none.

#### Validation (fail-loud at load, like queues)

- Unknown `driver` in a `harnesses:` entry → error naming the entry + known drivers.
- Agent referencing an unknown `harness` → error naming the agent + known harnesses.
- `api_key_file` that doesn't resolve to a readable file → clear error **at
  spawn** (not at load — the file may appear later), replacing today's silent
  skip in `LovelaiceDriver.extra_env`.

### 2. Drivers

#### OpenCode model selection — the core fix

`opencode acp` has no model flag; OpenCode reads its model from config. The
selection rides the **existing `extra_env()` seam**:

```python
class OpenCodeDriver(AcpDriver):
    BASE_CMD = ["opencode", "acp"]

    def extra_env(self, agent: Agent) -> dict[str, str]:
        model = getattr(agent, "model", "")
        if not model:
            return {}
        return {"OPENCODE_CONFIG_CONTENT": json.dumps({"model": model})}
```

**MUST be live-probed before finalizing** (repo real-model-probe discipline,
see `know-how/native-lovelaice-agent.md`). Two open questions the probe answers:

1. Does `OPENCODE_CONFIG_CONTENT` override the model in ACP mode, and does it
   **merge** with the repo `opencode.json` (preserving the `aegis` MCP block)
   or **replace** it? If it replaces, the injected JSON must re-include the MCP
   block (harmless — aegis already injects MCP per-session over ACP, but the
   repo `opencode.json` is what a bare `opencode` reads).
2. Fallback if `OPENCODE_CONFIG_CONTENT` is not honored: write a temp config
   file merging `{"model": …}` over the discovered project config and point
   `OPENCODE_CONFIG` (path env) at it, cleaned up on session close.

Either path stays within `extra_env`; no new driver shape.

#### Lovelaice / Gemini / Claude

Mechanically unchanged. The only difference: `base_url` / `api_key_file` are
read from the **resolved harness registration** (with the Lovelaice `Provider`
fields as fallback). `GeminiDriver` still injects `-m <model>`; `ClaudeDriver`
still maps effort.

### 3. Per-session selection

#### Interactive (TUI `picker.py` + web)

Two-tier `AgentPicker`:

- **Presets (fast path, unchanged muscle memory):** named `agents:` slugs on
  top. Selecting one spawns immediately with that profile's model/effort — the
  current behavior, byte-for-byte.
- **Custom path:** registered **harnesses** listed below a separator (or a
  `Tab`-switched pane). Selecting a harness opens a **model picker**, then an
  **effort picker**.

**Model catalog per harness** (what fills the model picker):

| harness driver | catalog source |
|----------------|----------------|
| `claude-code`, `gemini` | `models.yaml` registry, filtered by provider (existing, refreshable via `aegis models`) |
| `opencode` | `opencode models` output, cached ~24h (same cadence as the `models.yaml` background fetch) |
| `lovelaice` | endpoint-dependent → **free-text entry**, optional autocomplete by querying `{base_url}/models` (OpenAI-compatible) |

**Free-text is always allowed** on every harness, so a new/unknown model id is
never blocked by a stale catalog.

**Effort picker** is shown only when the resolved driver maps effort
(`claude-code` today). For opencode/gemini/lovelaice the step is skipped;
`effort` stays in the schema (default `high`, kept for logging) and drivers
that can't map it ignore it — the current status quo.

The picker resolves to a **transient `Agent`** (harness + chosen model + chosen
effort + permission) handed to the existing spawn path. It is not written to
`.aegis.yaml`.

#### Non-interactive spawn

- `aegis_spawn` MCP tool gains optional `model` / `effort` params (layered over
  the named profile it already takes).
- The spawn slash command gains optional `model` / `effort` args.

Queues / schedules / groups are untouched — they resolve named profiles only.

### 4. Config authoring

- `aegis config harness {add,list,remove}` — comment-preserving ruamel edits +
  atomic tempfile rename, identical machinery to the `agent` / `queue` verbs in
  `config/edit.py` + `cli_config.py`.
- TUI `ConfigPanel` / `AddAgentModal` become harness-aware: an agent is
  authored as *(registered harness + model + effort + permission)* rather than a
  raw driver string. Registering a harness is a new modal/verb.

## Data flow

```
.aegis.yaml
  harnesses: ──► HarnessRegistration{driver, base_url, api_key_file, default_model}
  agents:    ──► Agent{harness→ref, model, effort, permission}
                    │
   interactive spawn (picker) ─► transient Agent{harness, model*, effort*}
   aegis_spawn(model?, effort?) ─► transient Agent
                    │
                    ▼
        resolve harness → driver + credentials
                    │
                    ▼
        get_driver(driver).session(agent, …)
          - OpenCodeDriver.extra_env → OPENCODE_CONFIG_CONTENT={"model":…}
          - GeminiDriver.build_argv → -m <model>
          - LovelaiceDriver.extra_env → LOVELAICE_MODEL/BASE_URL + key
          - ClaudeDriver → effort mapping
```

## Testing

**Hermetic (`-m "not live"`):**

- `harnesses:` load + overlay merge + fail-loud on unknown driver.
- Implicit-harness back-compat: flat `harness: claude-code` and
  `provider: ClaudeCode(...)` still resolve; explicit registration overrides
  implicit.
- Agent → harness resolution, including `default_model` fallback and credential
  resolution order (harness first, Provider fallback).
- `OpenCodeDriver.extra_env` emits the expected `OPENCODE_CONFIG_CONTENT` JSON
  for a given `agent.model`, and emits nothing when `model` is empty.
- Picker catalog selection is pure/testable: given a harness + a stub catalog,
  the resolved transient `Agent` carries the chosen model/effort.

**Live (auto-skip when `opencode` off PATH):**

- OpenCode ACP round-trip on a **free** model, asserting the selection is
  honored (the model-selection probe, gated behind the `live` marker in
  `test_drivers_multiprovider_live.py`).

## Rollout

- TDD, commit per logical unit, straight to `main` (aegis convention).
- Ship in order: config model + validation → back-compat resolution → OpenCode
  `extra_env` + live probe → picker → non-interactive spawn params → config
  authoring verbs + ConfigPanel.
- The **vertical slice** that proves the whole thing end-to-end first:
  register an `opencode` harness, author a `fast-free` agent on a free model,
  spawn it, confirm the free model actually answers. Everything else layers on.

## Open items resolved before merge

- **OpenCode config-injection mechanism** — confirm `OPENCODE_CONFIG_CONTENT`
  vs temp-file `OPENCODE_CONFIG` by live probe; wire whichever works.
- **`{base_url}/models` autocomplete for lovelaice** — nice-to-have; free-text
  entry is the guaranteed path, autocomplete is best-effort.
