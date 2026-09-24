# Antigravity CLI as a harness — stream-json, not ACP

**Status:** designed 2026-09-24, not implemented. Every measurement below was
taken against `agy` 1.2.8 on zion on 2026-09-22.
**Scope:** one new driver, `src/aegis/drivers/antigravity.py`, plus its entry in
`DRIVERS`. Nothing about `AgentSession`, the MCP plane or the config schema
changes. The driver needs no new config key, which is the point of half this
document.

## Why now

Gemini CLI is being retired for personal accounts. Google announced the
transition on 2026-05-19, and **on 2026-06-18 Gemini CLI stopped serving Google
AI Pro, Ultra and free-tier requests**; those tiers moved to Antigravity CLI.
Gemini CLI survives for Gemini Code Assist Standard/Enterprise licences and paid
API keys only.

So `GeminiDriver` is not a driver we might extend. It is a driver whose
credential path was withdrawn from under it for the accounts most of our users
have. Antigravity is the replacement, and adding it is maintenance, not
expansion.

## What `agy` is

A Go binary, closed source, released weekly, with a public changelog and issue
tracker at `google-antigravity/antigravity-cli`. It shares its agent core with
the Antigravity 2.0 desktop app. The pieces we need arrived in a tight
sequence, all after 1.1.5:

| version | what landed |
|---|---|
| 1.1.6 | `--output-format` (`text`, `json`, `stream-json`) in print mode |
| 1.1.13 | `GEMINI_API_KEY` support; `modelProvider: "gemini"` in `settings.json` |
| 1.1.15 | `--input-format stream-json`, "so a driver can keep a session open" |
| 1.1.17 | `agy mcp add/remove/list/enable/disable` |
| 1.2.7 | headless timeout defaults to unlimited; `AGY_ERROR` on stderr, exit 3 |

That 1.1.15 changelog line is Google describing our use case in our words.
Their own docs ship a `subprocess.Popen` example headed "Drive a session
programmatically".

## Why not ACP

`agy` has no native ACP; the request is open as issue #31. Two third-party
adapters exist (`agy-acp`, `antigravity-acp`), both of which wrap the same
binary we would wrap.

Going through one would add a dependency and a translation layer in exchange
for **losing** information: `tool_info` (the tool's arguments and its output)
and per-step `usage` are richer in the native stream than what ACP's session
notifications carry. `AcpDriver` is the right base for OpenCode and Gemini
because those speak ACP natively. Here it would be a downgrade with extra
moving parts.

The native protocol is also, to a degree that reads as deliberate, Claude Code's
shape: NDJSON on stdin, one turn per message, a terminal event per turn. Even
the vocabulary matches — `control_request` is a message type `agy` recognises by
name in order to reject it.

## The protocol

One process, held open, `--input-format stream-json --output-format
stream-json`. Prompts in as `{"event":"user","message":{"content":"..."}}`,
one per line. Closing stdin ends the session.

Measured over a two-turn run with a tool call:

```
init: cid=1b8cc049-8a5e-4265-9de2-71e83e4ae470 perm=always-proceed tools=57
turn 1 -> 'pomegranate\n'
turn 2 -> tool[6] run_command {"CommandLine":"echo aegis_probe_marker"} out=null       (ACTIVE)
          tool[6] run_command {"CommandLine":"echo aegis_probe_marker"} out="…marker\n" (DONE)
          'Output: aegis_probe_marker; Fruit: pomegranate\n'
event counts: init=1, step_update=11, result=2   ·   one conversation_id throughout
```

Context crossed the turn boundary, one `result` per turn, one `init` for the
process, and the tool call arrived twice: announced with `output: null` while
running, completed with its output. That is exactly the pair `ClaudeDriver`
already renders.

Mapping onto `HarnessSession`:

| aegis needs | `agy` gives |
|---|---|
| `send(text)` | one NDJSON `user` event on stdin |
| `events()` | `init`, then `step_update` per step, then one `result` |
| tool calls | `step_update.tool_info`: `name`, `parameters`, `output`, `error` |
| subagents | `step_update.subagent_info` |
| usage | per-step and cumulative on `result` |
| `session_id` | `conversation_id`, present on the `init` event |
| `supports_resume` | yes — `--conversation <id>` / `-c` |
| `supports_fork` | no CLI flag found; the TUI has forks, print mode does not |
| `supports_idle_events` | **False** — exactly one `result` per turn, like ACP |
| permission bypass | `--dangerously-skip-permissions` → `permission_mode: always-proceed` |

### `interrupt()` is not implementable

Writing a `control_request` gets:

```
error: stream input message event "control_request" is not supported yet
```

and the session dies with **exit code 2**. There is no in-band abort. The driver
must leave `interrupt()` as the base-class no-op and rely on the caller
cancelling the read task and killing the process. This is the one capability
where Antigravity sits below Claude Code, and it should be stated in the
driver's module docstring rather than discovered.

### `result.status` does not mean what it says

**This is the trap most likely to produce a broken driver.** Both turns of the
run above reported `status=ERROR` while returning correct answers. The cause:

```
error = 'API error (attempt 1): Error 503, Message: This model is currently
         experiencing high demand… Status: UNAVAILABLE'
```

`agy` hit a transient 503 on its first attempt, retried, succeeded, delivered
the right response — and left `status` at `ERROR` carrying the *first attempt's*
error. A clean turn with no transient failure returns `SUCCESS` with `error`
empty, so the field is real; it just means "something went wrong at some point",
not "the turn failed".

A driver keying failure off `status == "ERROR"` marks healthy conversations as
broken, intermittently, only under load. **Treat a turn as successful when
`response` is non-empty and the final step is a `DONE` `agent_response`**, and
surface `result.error` as a warning rather than a failure.

### MCP tools are not enumerated

`init.tools` held the same 57 entries with and without an MCP server
configured. `agy` exposes MCP through a single dispatcher tool, `call_mcp_tool`,
rather than listing `mcp__server__tool` names the way Claude does. Anything in
aegis that renders or counts tools by name must not expect our MCP tools to
appear in the catalogue.

## Credentials: the driver's negative obligation

`agy` picks its credential mode entirely from its own configuration:

| mode | what the user does |
|---|---|
| subscription | `agy login` once. No `modelProvider` in `settings.json`. |
| API key | `modelProvider: "gemini"` in `settings.json`, `GEMINI_API_KEY` in env. |

**The driver must not write `settings.json` and must not set `GEMINI_API_KEY`.**
Then the mode is the user's decision, made where they already make it, and
`.aegis.yaml` needs no credential key at all. A user switches between a
subscription and an API key without aegis knowing it happened.

Only the API-key path was verified end to end (`SUCCESS`, correct response,
usage reported). The subscription path could not be exercised from Cuba: `agy`
refuses with

```
Eligibility check failed: Your current account is not eligible for Antigravity,
because it is not currently available in your location.
```

That refusal arrives with `num_turns: 0` and not one stream event emitted, so it
sits strictly before the point where the two credential modes could diverge, and
the protocol above is the same on both. That is an inference from where the gate
sits, not a green run. **Confirming it needs an account in an eligible
country, and it should be confirmed before we claim subscription support.**

Google's own FAQ recommends the API-key route for third-party agents, so it is
also the mode with no terms-of-service ambiguity. Section 6 of the Antigravity
terms forbids "using third party software, tools, or services to access the
Service"; at least nine threads on Google's own forum between 2026-07-09 and
2026-09-17 asked whether wrapping the official CLI counts, several describing
this exact architecture, and **Google answered none of them**. We take no
position: the driver works either way, the user chooses, and the docs should say
plainly that the subscription path is unclarified.

## Per-session MCP: relocate `HOME`

`agy` reads MCP configuration from two fixed paths, and takes no `--mcp-config`
flag:

- `~/.gemini/config/mcp_config.json` — global
- `<workspace>/.agents/mcp_config.json` — per directory

Per *directory* is not per *session*: two sessions in one repo would share a
token, which breaks the rule that only the brain mints a session's token.

`${VAR}` is not an escape hatch. A config carrying `${AEGIS_SESSION_TOKEN}` in
both `args` and `env` reached the server verbatim, and `agy mcp list` prints it
verbatim too. The parent environment *is* inherited by the MCP process, but a
config value is never expanded.

So each session gets its own `HOME`, holding a real `mcp_config.json` and
symlinks to everything else:

```
/run/aegis/agy/<handle>/.gemini/
  antigravity-cli -> ~/.gemini/antigravity-cli      # settings, conversations
  config/
    config.json   -> ~/.gemini/config/config.json
    projects      -> ~/.gemini/config/projects
    mcp_config.json                                  # real, per session
```

Verified: `agy mcp list` under that `HOME` reports the session's server as
`enabled`, and `settings.json` resolves to the user's real file with their
model and their trusted workspaces intact.

**This only works because credentials are not in `$HOME`.** A `HOME` built from
scratch, containing no credential file of any kind, reached the eligibility
check rather than asking for a login — `agy` keeps its token in the system
keyring. Had it been otherwise, every session would demand a fresh sign-in and
this design would be dead. Any future release that moves tokens into `$HOME`
breaks it, which is worth a test.

## `model` should usually be omitted

`agy` takes catalogue display names, not model IDs: `"Gemini 3.8 Flash (Low)"`,
`"Gemini 3.1 Pro (High)"`. `--model gemini-2.5-flash` is refused. The catalogue
varies by credential mode and by plan, so a name pinned in an agent profile can
be absent for a given user.

Default to **not passing `--model`**, letting `agy` use the model in the user's
own `settings.json` (which the symlink already provides), and pass `--model`
only when the profile sets one explicitly. `--effort` maps cleanly onto
`Agent.effort` (`low|medium|high`).

`customModels` exists in `settings.json` — the binary carries the validation
message `customModels[%s]: modelName is required` — but the schema is
undocumented and a plausible guess was rejected. Out of scope.

## Open questions

- **Concurrent sessions share one conversations database.** Symlinking
  `antigravity-cli` is what puts a session's transcript in the user's own `agy`,
  which we want, but it points every session at one SQLite file. Untested with
  two sessions at once, and the first thing likely to break.
- **The subscription path is unverified**, as above.
- **`supports_fork`**: 1.2.8 fixes bugs in forked conversations, so the concept
  exists; no print-mode flag was found for it.
- **`AGY_ERROR` on stderr with exit 3** (1.2.7) is a second, structured failure
  channel we have not exercised.

## What done means

Beyond the repo's usual gates: a session opened against a real `agy`, two turns
with a tool call rendered in the TUI, the aegis MCP tools reachable from inside
that session, and a turn that survives a transient 503 without being reported as
failed.
