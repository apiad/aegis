# Switching a live session's model and effort

> **Status:** design, 2026-10-04. Planned: `docs/superpowers/plans/2026-10-04-aegis-live-model-and-effort-switch.md`.
> Issue: [#97](https://github.com/apiad/aegis/issues/97).
> Parent: `2026-07-17-aegis-slash-commands-2b-builtin-coverage-design.md`, which
> deferred `/model` and `/effort` on the premise that changing either needs a
> resume-restart. That premise is wrong for both harnesses measured below.

`/model <name>` and `/effort <level>` change the model and reasoning effort of the
session in the active tab. The conversation stays in the same harness process:
no restart, no `--resume`, no lost context. The next turn runs on the new
setting, and the setting survives a daemon restart.

## Where aegis stands today

aegis has neither command. `dispatch()` stops any verb it does not own with
`unknown command` (`src/aegis/commands/__init__.py:146`), so `/model` and
`/effort` never reach the harness. The one way through is the escape the input
box already has: `//model sonnet` delivers `/model sonnet` as a plain message
(`src/aegis/tui/pane.py`, the `"//foo"` branch after `dispatch`). For Claude that
does switch the model. aegis does not know it happened, so the status bar keeps
the old name and the next resume rebuilds the old `--model` argv. For OpenCode
it does nothing at all (see below).

Model and effort are fixed at spawn in three places:

- `ClaudeDriver.build_argv` bakes `--model` and `--effort` into the argv
  (`src/aegis/drivers/claude.py:331-334`), and `resume()` reuses that argv.
- `OpenCodeDriver.extra_env` passes the model as `OPENCODE_CONFIG_CONTENT`
  (`src/aegis/drivers/opencode.py`). Effort is not passed to OpenCode at all.
- `StatusBar.__init__` captures model and effort once
  (`src/aegis/tui/widgets.py:423`, built at `src/aegis/tui/pane.py:1197`).

Resume after a daemon restart looks up the profile from config,
`agents[tab.profile]` (`src/aegis/tui/app.py:204` and `:929`), so even today's
`/spawn --model` override is lost on restart. `WorkspaceTab`
(`src/aegis/state/workspace.py:35`) has no field to carry it.

## What the harnesses accept

Measured on zion on 2026-10-04 with Claude Code 2.1.283 and OpenCode 1.18.31,
each driven the way aegis drives it (`claude -p` stream-json; `opencode acp`).
The probe scripts and raw JSONL are in the Workspace at
`.playground/model-switch/`.

### Claude Code

Started on `claude-haiku-4-5-20251001`, then switched mid-session.

| Sent | What happened |
|---|---|
| user text `/model claude-sonnet-5` | A turn with a `<synthetic>` assistant reply, "Set model to Sonnet 5 for this session only", and a `result`. The next turn's `init` and `assistant.model` were `claude-sonnet-5`. |
| `control_request {"subtype":"set_model","model":…}` | `control_response success`, nothing in the transcript. The next turn ran on the new model. |
| `set_model` with `no-such-model-xyz` | `control_response error`, "Model 'no-such-model-xyz' not found", after 4.7 s. |
| `set_model` sent 3 s into a streaming turn | `success` 1.8 s later. The turn in flight finished on the old model; the next turn used the new one. |
| user text `/effort low` | A synthetic turn, "Set effort level to low (this session only)". |
| `control_request {"subtype":"apply_flag_settings","settings":{"effortLevel":L}}` | `success` for `low`, `medium`, `xhigh` and `max`; `get_settings` then reports `applied.effort == L`. |
| `apply_flag_settings` with `effortLevel: "bogus"` | `success`, but `applied.effort` stays at the previous value. The CLI ignores an unknown level silently. |
| `get_settings` right after spawn with `--effort high` | `applied.effort: "high"` on Sonnet 5, `null` on Haiku 4.5. |

One `set_model` to Haiku failed once with "Couldn't confirm model … with the
API", and the same call succeeded on the next run. The CLI checks the model
against the API before it switches, so a switch can fail for reasons aegis does
not control, and the error has to reach the operator.

### OpenCode (ACP)

`session/new` and `session/load` both return `configOptions`. On this install
there were three:

| `id` | `category` | values |
|---|---|---|
| `model` | `model` | 148, e.g. `opencode-go/deepseek-v4-flash` |
| `effort` | `thought_level` | depends on the model: `low/high/max/default` on deepseek-v4-flash, `low/medium/xhigh/default` on qwen3.8-flash |
| `mode` | `mode` | `build`, `plan` |

| Sent | What happened |
|---|---|
| prompt text `/model opencode-go/qwen3.8-flash` | `end_turn` with no output and no tokens. The next turn read deepseek's prompt cache, so nothing switched. `model` is not in `available_commands`. |
| `session/set_config_option {configId:"model", value}` | Returned the updated `configOptions` and emitted `config_option_update`. The next turn wrote a fresh 13k-token cache, so a different model served it. |
| `session/set_config_option {configId:"effort", value:"high"}` on qwen3.8-flash | JSON-RPC `-32602`, "effort not found: high". |
| the same with `medium` | Applied; `configOptions` showed `effort: medium`. |
| `session/set_model` (the older, unstable method) | Also applied. |
| new process, env still naming deepseek, `session/load` of the switched session | `configOptions` came back as `qwen3.8-flash` / `medium`, and the turn read qwen's cache. OpenCode stores both per session. |

`acp` 0.10.0, the client aegis pins, has `ClientSideConnection.set_config_option`.

### Model lists, and how fast they come

Autocomplete for `/model` needs the list of models. Every source was timed on the
same day:

| Source | Time | What it returns |
|---|---|---|
| Claude `control_request {"subtype":"list_models"}` on a live session | 8 to 13 ms, three calls | 12 models; works without `initialize` |
| Claude `control_request {"subtype":"initialize"}` | 542 ms, mostly CLI boot | the same list minus disabled entries (11), in `response.models` |
| OpenCode `session/new` | 1,265 ms, which aegis already pays at spawn | 148 models in `configOptions`, a 12 KB payload |
| `opencode models` as a separate process | 2,786 ms | 148 lines |

Each Claude entry carries `value` (an alias such as `sonnet` or a full id),
`resolvedModel`, `displayName`, `description`, and `supportedEffortLevels`.
The levels differ by model: `low medium high xhigh max` on the Opus, Fable and
Sonnet 5 families, `low medium high max` on older ones, and none on Haiku 4.5.
That explains the `applied.effort: null` on Haiku above. `list_models` also
returns entries with `"disabled": true` that the installed CLI cannot use yet
("Update Claude Code to use this model").

## Design

### The commands

```
/model [<name>]
/effort [<level>]
```

Both act on the calling pane's session (`CommandContext.handle`). With no
argument they print the current value and, where the harness lists them, the
choices. With an argument they switch and print what the harness confirmed,
for example `model → claude-sonnet-5 (from the next turn)`.

They are aegis built-ins, not a pass-through of the text to the harness. Text
pass-through works for Claude only. OpenCode drops it without a word, and in
both cases aegis would not learn the new value.

### Driver method

`HarnessSession` gains one method with a default that refuses:

```python
async def set_option(self, kind: str, value: str) -> OptionResult: ...
```

`kind` is `"model"` or `"effort"`. `OptionResult` carries `ok`, the value the
harness reports as applied, and an error string. The base implementation
returns `OptionResult(ok=False, error="cannot switch <kind> in a live session")`,
and `AgentSession` prefixes every error with the harness name. That covers the
oneshot driver and any harness that has not been checked.

A harness process starts lazily, on the session's first turn. `/model` typed into
a fresh tab starts it through the same path the turn uses, so the two cannot
start it twice.

**Claude** (`ClaudeSession`). Sends a control request and waits for the
`control_response` with the same `request_id`:

- model: `{"subtype": "set_model", "model": value}`.
- effort: refused before anything is sent when `value` is not in the current
  model's `supportedEffortLevels`, with "<model> does not take an effort level"
  when that list is empty. Otherwise
  `{"subtype": "apply_flag_settings", "settings": {"effortLevel": value}}`,
  then `{"subtype": "get_settings"}`. The result is `ok` only if
  `applied.effort == value`. The read-back stays even with the check in front,
  because an unknown level returns `success` and changes nothing, and the next
  CLI release may rename a level.

The driver does not read `control_response` today: `interrupt()` writes its
request and drains events without matching a reply. `_pump_stdout` has to
route a `control_response` line to a pending future keyed by `request_id`
before `parse()` sees it, so the reply cannot leak into a turn's events. Bound
the wait at 15 s, since a bad model took 4.7 s to come back.

**ACP** (`AcpSession`, which covers OpenCode, Gemini and lovelaice). Keep the
`configOptions` from the `new_session` / `load_session` response and from every
`config_option_update`, instead of dropping them as the client does now (the
"Other update classes … drop" branch in `_AegisAcpClient.session_update`).
Find the option by `category`, not by `id`, because the agent chooses the ids:
`model` for model, `thought_level` for effort. Then:

- If the harness advertises no option of that category, refuse with
  "<harness> does not offer a <kind> setting".
- Validate `value` against the option's current `options`. Effort levels change
  with the model, so the check must use the list as it stands after the last
  `config_option_update`, not the list from boot. An exact match wins.
  Otherwise a value that is the unique suffix of one option after its `/` is
  accepted, so `/model qwen3.8-flash` finds `opencode-go/qwen3.8-flash`.
  Anything else is an error that lists the near matches.
- Call `set_config_option(config_id=<option id>, value=…, session_id=…)` and
  report the `currentValue` from the response.

Only OpenCode has been measured. Gemini and lovelaice take the same path and
get the refusal if they advertise no such option. Gemini could not be probed:
on zion, `gemini --acp` 0.44.0 answers `session/new` with "This client is no
longer supported for Gemini Code Assist for individuals", which is the account
problem behind #116. A fake ACP agent with no config options stands in for it
in the tests. Adding config options to lovelaice is a separate change.

The bundled model registry (`aegis.models.models_for`, which feeds the spawn
picker and the price table) is not a source for `/model`. It lists what aegis
can price, and the live session lists what the harness will accept, which is
the question `/model` asks.

### Autocomplete

`HarnessSession` gains a second method, synchronous and without I/O:

```python
def option_choices(self, kind: str) -> list[OptionChoice]: ...
```

It returns the list the session last received, so the command palette can call
it on every keystroke. `OptionChoice` holds the `value` to submit, a display
name and a one-line description. The default returns `[]`, and an empty list
means "no completions", never an error.

There is no cache on disk or across sessions. Each list lives on the harness
session that produced it and dies with it:

- **Claude** sends `list_models` once, after the first `SystemInit`, and again
  after every confirmed switch. Entries with `disabled: true` are dropped. The
  same response supplies the `/effort` choices: the `supportedEffortLevels` of
  the entry whose `value` or `resolvedModel` matches the current model.
- **ACP** reads the `options` of the `model` and `thought_level` config options
  it already keeps for validation. `config_option_update` refreshes them, which
  is how `/effort` completions follow a model switch on OpenCode.

Asking for completions in a tab whose harness has not started yet starts it in
the background. Until the first list arrives, `/model ` completes nothing: about
half a second on Claude and four seconds on OpenCode, measured from the harness
start. That is the only window. A disk cache would fill it at the cost of
a list that goes stale when the CLI updates, as the disabled Sonnet 5.5 entry
shows, and it is not worth that.

The palette already completes arguments: an `Arg` takes a `completer`
(`src/aegis/commands/args.py`), and `complete()` ranks its choices with
`fuzzy_rank` (`src/aegis/commands/__init__.py:234`). What it lacks is the
session. A completer is called with the bridge alone, so it cannot tell whose
models to list. `complete()` gains the calling handle and passes it to a
completer that accepts two arguments. The pane already knows its handle when it
calls `complete()`. The `/model` completer then calls a bridge method,
`session_option_choices(handle, kind)`, and maps each `OptionChoice` to a
`(value, detail)` pair, the shape completers return today.

What `/model` accepts on submit is still the validation rule above: exact, or
the unique suffix after the provider's `/`.

### Session state and persistence

A confirmed switch replaces `AgentSession.agent` with
`_overlay_agent(agent, model=…, effort=…)` (`src/aegis/core/manager.py:25`).
Everything that reads `session.agent` sees the new value from then on, including
`/fork`, the recap and the session's own resume.

The labels are stored in two places, each following an existing pattern:

- `WorkspaceTab` gains `model: str | None = None` and `effort: str | None = None`,
  written by `_pane_to_tab` on every snapshot. This is the boot roster. With
  defaults of `None`, an old `workspace.json` loads unchanged and
  `WORKSPACE_VERSION` stays at 1.
- `SessionMeta` gains `model` and `effort` (default `""`), appended to the
  session log on each switch the way `_record_title` appends a title. The
  history fold reads the last non-empty value into `SessionHistoryRow`, so a
  session reopened from Ctrl+R comes back on its switched model too. The
  history index keeps `INDEX_VERSION = 2`, for the reason its comment gives.

`src/aegis/tui/app.py` resumes in five places: `bootstrap_resume`, both
branches of `_resume_agent_tabs` (brain and local), and both branches of
`_resume_from_history`. All five take the stored values through one helper that
returns the `model` and `effort` overrides. The local branches overlay them on
`agents[tab.profile]` before `drv.resume`. The brain branches pass them to
`SessionManager._sync_spawn`, which already accepts `model` and `effort` next to
`resume_from`. This also fixes the existing loss of `/spawn --model` overrides
on restart.

Claude needs the overlay, because `--resume` rebuilds the argv from the agent.
OpenCode restores both values from its own session store, as measured. The
overlay keeps aegis's record and the status bar right, and it is harmless.

`Effort` (`src/aegis/config/__init__.py:82`) gains `xhigh`, which Claude accepts
(measured above), and `_EFFORT` in `drivers/claude.py` maps it. An ACP effort
value that is not an `Effort` member, such as OpenCode's `default`, is recorded
on the tab for display and left out of the `Agent` overlay. The harness
restores it on load anyway.

### Display

`StatusBar` gains a method that rebuilds `_identity` from a model and an
effort. The status bar and the sidebar stop reading the pane's copy of the
`Agent` and read two labels on the `AgentSession`, `model_label` and
`effort_label`. Both start from the agent and change when a switch is confirmed.

ACP sessions also report the values the harness actually runs. At start and on
every `config_option_update`, `AcpSession` emits a `ContextUpdate` with two new
fields, `model` and `effort`, and `AgentSession` folds them into the labels.
This fixes an existing error: an OpenCode tab's status bar shows the profile's
`effort` (default `high`), which aegis never passes to OpenCode, while OpenCode
runs whatever its own config says (`low` in every probe above).

Claude reports nothing extra. A `SystemInit.model` that differs from the label
is ignored, because the CLI resolves aliases (`opus` comes back as a full id)
and because `usage/aggregate.py` already reads that field for pricing. ACP
`SystemInit.model` carries the agent's name (`OpenCode`) and stays as it is.

The web client renders the TUI over a terminal relay, so it gets the same
status bar with no extra work.

### Through the bridge

The commands call a new `AppBridge` method,
`set_session_option(handle, kind, value) -> dict`, implemented on
`SessionManager` and on the TUI app, as `set_title` is
(`src/aegis/core/manager.py:731`, `src/aegis/tui/app.py:3134`). It returns
`{"ok": True, "model": …, "effort": …}` or `{"error": …}`, the same shape the
other bridge methods return.

Mid-turn, the call goes to the harness immediately, with no hold or queue in
aegis. Both harnesses accept it and apply it from the next turn. OpenCode
answered `set_config_option` 2.0 s into a streaming prompt with the new model as
`currentValue`. The prompt in flight finished on deepseek, which its cache read
shows, and the next turn wrote a fresh cache on qwen.

## Out of scope

- An MCP tool that lets an agent switch its own model. It belongs in a separate
  issue once there is a reason to want it.
- Passing every unknown `/verb` through to the harness. That would reach Claude's
  other built-ins, but OpenCode ignores the text, so it cannot be how these two
  commands work.
- Completing `--model` on `/spawn`. No session exists yet to ask, and a list
  fetched for it would need the disk cache this design rejects.
- `mode` (OpenCode's build and plan). Same mechanism, different command.
- Persisting a switch into `.aegis.yaml`. A switch belongs to the session, as
  `/spawn --model` does.

## Done

1. In the TUI, a Claude tab spawned on Haiku: `/model claude-sonnet-5`, one turn,
   and the turn's `assistant.model` in the session log is `claude-sonnet-5`. The
   status bar shows it before that turn starts.
2. `/effort xhigh` on that tab reports `xhigh`, and `/effort bogus` is refused
   without a request reaching the harness.
3. An OpenCode tab: `/model qwen3.8-flash` resolves to
   `opencode-go/qwen3.8-flash`, and `/effort high` is refused with the levels
   the new model offers.
4. Kill the daemon, start it again, and the resumed Claude tab is still on
   Sonnet 5, read from the `init` of its next turn.
5. An ACP session whose agent advertises no config options (the Gemini case,
   tested with a fake agent) gets a refusal from `/model`, never a silent no-op.
6. Typing `/model so` in a Claude tab offers `sonnet` and the Sonnet ids, and
   no disabled entry. Typing `/effort ` in an OpenCode tab after a switch to
   qwen3.8-flash offers `low medium xhigh default`.
7. Driver tests cover the `control_response` routing, the effort read-back and
   the ACP category lookup against recorded fixtures from the probes. A switch
   that the harness rejects must turn the test red.
