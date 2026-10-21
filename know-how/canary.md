---
when: a `canary` issue was opened, a harness released a version that may break aegis, a release is about to be cut, or you are adding a scenario to tests/canary/ — running the real harnesses against the fake model, reading a canary report, and teaching the fake a harness's new protocol
---

# The canary

The rest of the suite drives stand-ins (`tests/fake_claude.py`,
`fake_codex.py`, `fake_opencode.py`), which speak what the harness spoke the
day each was written. The canary runs the **published** Claude Code, Codex and
OpenCode against a **fake model** (`tests/canary/fakemodel.py`), so a harness
release that changes its protocol, its config or its tool names shows up as a
red test the next morning instead of as a person's broken session. No model is
called and no quota is spent.

## Run it

```bash
scripts/canary.sh                                   # everything, ~3 min on 4 workers
scripts/canary.sh -k "read_session and claude"      # one scenario, one setup
scripts/canary.sh 'tests/canary/test_harnesses.py::test_x[codex-openai]'
AEGIS_CANARY_NO_INSTALL=1 scripts/canary.sh ...     # reuse what is installed
```

It installs `@anthropic-ai/claude-code`, `@openai/codex` and `@opencode/cli`
at `latest` into `~/.cache/aegis-canary/npm`, and `opencode-ai` (OpenCode 1,
what `opencode.ai/install` gives) into `~/.cache/aegis-canary/oc1`, never over
the ones on PATH. To pin a version, install it there and pass
`AEGIS_CANARY_NO_INSTALL=1`. `AEGIS_CANARY_VERSIONS=min` or `max` installs
an end of the tested range instead, into `npm-min` or `npm-max`
(`releasing.md`, *Moving the tested harness range*). The canary's server turns
aegis's version gate off, and `versions.txt` marks each version `in_range` or
`newer`. Comparing two versions of one harness is how a
regression is proved to be the harness's: on 2026-10-16 the read-session test
passed on Claude Code 2.1.296 and failed on 2.1.301 (#442).

Run several workers on a quiet machine only. At a load average of 29 a run
reported 62 failures that a rerun a few minutes later did not.

## Read a failure

Each failing test writes `.canary/<test>.json`, and CI turns it into the
issue: the harness version, the error, the fake model's request log, every
session's transcript and the harness's stderr. Read the request log first:

- **`CANARY: no tool ending in 'x' was offered`**: the harness did not offer
  aegis's tool. Before blaming aegis, look at the request's `tools`,
  `tool_types` and `special_tools`: Codex on OpenAI's own models hides MCP
  tools behind a client-run `tool_search`, and Claude Code can defer them
  behind `ToolSearch`. The fake searches when it finds a search tool it knows.
- **A tool result that is an error** (`results=` in the issue): the harness
  offered the tool and refused or failed the call. This is the usual shape of
  a real break (#442).
- **No request at all**: the harness never reached the fake. Its stderr says
  why: a config key it no longer reads, a login it wants.

## When a harness changes its wire

The fake speaks the Anthropic Messages API (Claude Code), the OpenAI Responses
API (Codex) and Chat Completions (OpenCode's OpenAI-compatible provider). A new
item type, such as Codex's `tool_search_call` or its `compaction_trigger`, is
taught to the fake from the harness's own schema (Codex publishes
`codex-rs/app-server-protocol/schema/typescript/ResponseItem.ts`), never by
weakening a test.

## Add a scenario

A scenario is one test in `tests/canary/test_harnesses.py`, run once per setup
(`SETUPS` in `conftest.py`). Script the fake with `Rule`s, keyed by a marker
word (`CANARY-...`) in the person's message, drive the session the way a
person does (`canary.send`, which calls `session.send` like the browser), and
assert on what aegis keeps: the transcript, the card, the monitors. A marker
that also appears in a reply or a worker's result picks the wrong rule: the
latest marker in the message wins.
