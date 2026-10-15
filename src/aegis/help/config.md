---
title: Configuration
kind: concept
when: adding or changing agents, the default agent, queues, recaps, side notes, the per-session memory cap, parking idle sessions or the network readings in .aegis.yaml, or picking a theme
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
btw: {agent: opus}
context: {handoff_at: 300000, cold_after: 3600, cold_min: 100000}
park: {claude-code: 1200}
```

## Add an agent

`agents:` maps a name to a preset. Each one has:

- `harness:` one of claude-code, opencode or codex. aegis drives the latest
  stable release of each; for OpenCode that is OpenCode 2
  (`npm install -g @opencode/cli`), and `aegis doctor` names an older one;
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
missing either is reported and takes no tasks. `quiet:` is true unless set to
false: a worker then has no tab, only a dimmed card in the Fleet, unless it
fails. See the queues page for how
tasks run.

## Recaps

`recap:` with `agent:` names a Claude Code agent that writes a short recap
when a person returns to a tab with a long unread stretch. Without it, recaps
are off. A cheap, fast agent is enough.

## Side notes

`btw:` with `agent:` names a Claude Code agent that answers `/btw` side
questions. It reads up to the last ten turns of the session, about 32,000
tokens, and the side notes asked before. Without it, `/btw` says which line to
add. A side note may need more model than a recap, so the two keys are separate.

## Context size and the cache

`context:` sets three thresholds, and every one has a default. `handoff_at:`
(300000) is the context size, in tokens, from which `turn_end` tells an agent
how large its context is, so that it can write a brief for a fresh session at
a natural break. 0 turns that line off.

`cold_after:` (3600 seconds) and `cold_min:` (100000 tokens) decide when a
session's tab warns that its prompt cache has expired: idle at least
`cold_after:`, with a context of at least `cold_min:`. The next message then
rewrites the whole context, as much as about 20 warm turns read. The warning
covers Claude Code only, because only its cache lifetime was measured.

A `context:` block with a wrong key or value leaves all three defaults in
force, and `aegis doctor` names the mistake.

## Memory per session

`session_memory_max:` caps the memory one session may use: its agent and
everything the agent starts, such as test runs, browsers and builds. It reads
like systemd's `MemoryMax`: a size (`3G`, `512M`), a share of the host's RAM
(`40%`, the default), or `max` for no cap.

```yaml
session_memory_max: 3G
```

A session that goes past it hits an out-of-memory kill inside its own
cgroup, which takes its largest process, usually a runaway test worker, and
leaves the agent, the server and every other session running. A session
under a cap gets no swap, so it is killed at the cap instead of paging the
host into the ground. A new value applies to
the next process a session starts.

The cap needs a cgroup per session, which aegis makes only when it runs as a
systemd service with `Delegate=yes`. Anywhere else, every session shares the
server's cgroup, as before, and the key has no effect. A value that is not a
size leaves the default in force, and `aegis doctor` names the mistake.

## Parking idle sessions

`park:` maps a harness to the seconds an idle session of it must be safe to
end before aegis ends its process to free its memory. The next message
resumes it. The sessions page says what makes a session safe to end.

```yaml
park:
  claude-code: 1200   # 20 minutes
```

A harness the block does not name never parks, and without the block nothing
does. A block with a harness aegis does not know, or a value that is not a
positive whole number of seconds, parks nothing, and `aegis doctor` names the
mistake. Only an admin can change it, in the file or in the Parking section
of the Settings page, which takes minutes and which members do not see.

Only Claude Code's resume has been measured. Codex and OpenCode sessions park
when named, and `aegis doctor` warns that how they resume is unmeasured.

## Network readings

Under CPU, RAM and disk, the Host rows of the Fleet band and of a session's
sidebar show three readings of the server's network:

- **Net**: the time a TCP handshake to 1.1.1.1 or 8.8.8.8 on port 443 takes,
  or `no internet` when neither answers.
- **Exit**: the address the server's traffic leaves from, with the Cloudflare
  location and country it reaches, from `cloudflare.com/cdn-cgi/trace`.
- **Speed**: the rate of a timed 1 MB download from `speed.cloudflare.com`, in
  megabits a second. Hover it for MB/s and the bytes it was measured on; click
  it to measure now.

They run only while a browser shows the server, and each linked server shows
its own. A reading that may no longer hold keeps its value, greyed, with its
age: the exit address after a failed lookup, everything after egress drops.
`network:` changes what they reach and how often, and every key is optional:

```yaml
network:
  enabled: true          # false: no readings and no rows
  interval: 20           # seconds between egress checks
  trace_interval: 300    # seconds between exit-address lookups
  speed_interval: 900    # seconds between speed tests; 0 turns them off
  speed_bytes: 1000000   # bytes each speed test downloads
  timeout: 3             # seconds for a handshake or a lookup
  speed_timeout: 30      # seconds for the whole download
  anchors: ["1.1.1.1:443", "8.8.8.8:443"]   # an IPv6 one as "[host]:port"
```

A block with an unknown key, or a value out of range, keeps every default,

## Workflows

`workflows:` bounds the node sessions every workflow on this server may have
open at once. `max_agents:` holds `total`, the bound for all agents together,
and a number for any agent alias that needs a bound of its own. An alias with
no number of its own is bound only by `total`. Without the block the bound is
`{total: 4}`, and a bare number is read as the total.

```yaml
workflows:
  max_agents: {total: 10, opus: 5, haiku: 2}
```

A workflow's own `limits`, of the same shape, can only lower these for that
run. Each session's memory is still capped by `session_memory_max:`, so the
most memory workflows can take is about `total` times that. A wrong block
keeps `{total: 4}`, and `aegis doctor` names the mistake, an alias that is not
an agent included.

## Terminals

`terminals:` maps a profile's name to `cmd:` and `cwd:`, with `restart:` and
`env:` when you need them. The + view offers each profile beside the built-in
shell. A profile with a mistake is offered with it and cannot be opened; the
others still can, and `aegis doctor` names the mistake. See the terminals page.

`terminal_memory_max:` caps the memory of each terminal's processes, as
`session_memory_max:` caps a session's, and takes the same values. Its default
is the session cap's, 40%.

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
