---
title: Configuration
kind: concept
when: adding or changing agents, the default agent, queues and recaps in .aegis.yaml, or picking a theme
---

aegis reads one file, `.aegis.yaml`, at the root it serves: `--root` when
given, else the nearest directory at or above where you started `aegis` that
holds one, else that directory itself. The
file says which agents a session can start from and which queues run tasks.
Nothing in it is a default: every agent names its harness, model, effort and
permission, and one that leaves a field out is reported by name and cannot be
started.

## A complete file

```yaml
default_agent: opus
agents:
  opus: {harness: claude-code, model: opus, effort: high, permission: full}
  quick: {harness: claude-code, model: haiku, effort: low, permission: write}
  deepseek: {harness: opencode, model: opencode-go/deepseek-v4-pro, effort: high, permission: full}
  reviewer: {harness: claude-code, model: opus, effort: max, permission: read, priming: You review code and change nothing.}
queues:
  general: {agent: opus, max_parallel: 3}
recap: {agent: quick}
```

## Add an agent

`agents:` maps a name to a preset. Each one has:

- `harness:` one of claude-code, opencode or codex;
- `model:` the model to run, such as opus or haiku for Claude Code. OpenCode
  and Codex models are written provider/model;
- `effort:` low, medium, high, xhigh or max;
- `permission:` read, write, auto or full (the sessions page says what each
  allows);
- `priming:` (optional) a system prompt every session from this agent gets.

The harness can also be written as `provider:` with the harness's name, or as
a nested `provider:` map whose `name` is the harness and whose other keys are
the fields. aegis keeps whichever form the file uses.

A session starts from one agent and can override its harness, model, effort
and permission, from the chips on the new-tab page or in `session_spawn`, but
never its priming. To make an agent run on haiku, set its `model:`, or override
the model for one session.

## The default agent

`default_agent:` names the agent `session_spawn` starts from when it is given
none, and the one the new-tab page picks first in a browser that has not
started a session yet.

## Queues

`queues:` maps a queue's name to `agent:` (the agent its workers start from)
and `max_parallel:` (how many workers run at once, a positive number). A queue
missing either is reported and takes no tasks. See the queues page for how
tasks run.

## Recaps

`recap:` with `agent:` names a Claude Code agent that writes a short recap
when a person returns to a tab with a long unread stretch. Without it, recaps
are off. A cheap, fast agent is enough.

## Editing it

The file is the configuration. A running server notices a change within a
second and uses it, whether you saved it in an editor or on the Settings page,
with no restart. A file that does not parse leaves the last one that did in
force, and says so, so a half-typed edit never stops a spawn. A deleted file
means no agents and no queues.

The Settings page edits the same file as a form: agents with their chips and
priming, which one is the default, and the queues. It keeps the file's
comments and key order, and refuses to save over a change made on disk since
you opened it. Only an admin can save.

`aegis init` writes a first file from the harnesses installed on this machine,
proposing agents for Claude Code and OpenCode and asking about each (`aegis
init --yes` takes every proposal).
It refuses to overwrite an existing `.aegis.yaml`.

## The theme

The theme is not in the file: each browser keeps its own. The Settings page
starts with a row for this browser, where the Theme chip picks one of ten:
Ink, Logbook, Electric, Phosphor, Blueprint, Amphora, Riso, Solari, Aizome and
Poster. The whole page changes at once, the colours of code in the transcript
and the pages agents send included.

## Check it

`aegis doctor` checks the file against the harnesses actually installed: that
it parses, that it has no key aegis does not read, that each harness an agent
names is on the PATH with its version and models, that each agent's model and
effort are ones its harness offers, that `default_agent:` names a usable
agent, that each queue is complete, and that the state directory is
writable. It prints one line per finding and exits 1
when any is an error. An agent can run the same checks with `config_doctor`,
and the Settings page has a Run doctor button.
