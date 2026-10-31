---
title: Configuration
kind: concept
when: adding or changing agents, the default agent, queues, recaps, side notes, the per-session memory cap, notices about disk, memory, CPU and quota, an agent's status and stalled monitors, when aegis nudges a silent agent, parking idle sessions, untested harness versions, search embeddings for skills and memories, the disks the Host rows list or the network readings in .aegis.yaml, or picking a theme
hints:
  - Add an agent with its own harness, model and effort in .aegis.yaml. #add-an-agent
  - A cascade starts on a cheap model and moves up when the task needs it. #cascades
  - Idle sessions park to free memory and wake when a message arrives. #parking-idle-sessions
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
notices: {disk: {on: 90, critical: 97, clear: 85}, cpu: false}
park: {claude-code: 1200}
```

## Add an agent

To add an agent to `.aegis.yaml`, map its name under `agents:` to a preset.
Each one has:

- `harness:` one of claude-code, opencode, codex or cursor. Each aegis release
  is tested with a range of versions of each harness; for OpenCode that is
  OpenCode 2 (`npm install -g @opencode/cli`). A version outside the range is
  named, with the command that installs a tested one (below);
- `model:` the model to run, such as opus or haiku for Claude Code. OpenCode
  and Codex models are written provider/model, Cursor models by their
  catalog's plain id (an id's own bracketed overrides — effort, context,
  flags — come with it when it is set). `models:` instead names an
  ordered list, a cascade (below);
- `effort:` low, medium, high, xhigh or max; a Cursor model carries the effort
  inside its id, so a change while it runs is accepted only when it is that
  same effort, and any other is refused;
- `permission:` read, write, auto or full (the sessions page says what each
  allows);
- `priming:` (optional) a system prompt every session from this agent gets;
- `keep_harness_tools:` (optional, Claude Code) a list of Claude Code tools
  this agent keeps although aegis replaces them (below).

The harness can also be written as `provider:` with the harness's name, or as
a nested `provider:` map whose `name` is the harness and whose other keys are
the fields. aegis keeps whichever form the file uses.

A session starts from one agent and can override its harness, model, effort
and permission, from the chips on the new-tab page or in `session_spawn`, but
never its priming. To make an agent run on haiku, set its `model:`, or override
the model for one session.

## Cascades

`models:` gives an agent an ordered list of models, best first, all on the
agent's harness:

```yaml
agents:
  free:
    harness: opencode
    models: [opencode/kimi-k2.5-free, opencode/glm-5-free, opencode/big-pickle]
    effort: high
    permission: full
```

`model: x` is a cascade of one, and an agent gives `model` or `models`, never
both. A model listed twice is a warning, and the repeat is ignored.
`aegis doctor` warns when a cascade puts a model above one that scores
higher on [Artificial Analysis](https://artificialanalysis.ai)'s Intelligence Index, the Coding Index
breaking ties, naming the first such pair:
"b (50.1) is above a (54.0) in the ranking", or "x (unranked) is above a
(54.0) in the ranking" for a model with no Intelligence Index. The scores
ship with aegis and are refreshed at each release, so a model released since
is unranked. The order is still yours: the warning changes nothing.

Every start from the agent (a new tab, `session_spawn`, a handoff, a queue
worker, a workflow node, a schedule) runs the first model that is not cooling
and whose quota gate is open. When none is, the start is refused or held as
for a closed gate, with the time the first model is free again. A spawn that
overrides the model runs that model alone, with no cascade.

When a turn fails because the model ran out of quota, hit a rate limit, timed
out, was overloaded, or is no longer served, the session falls to the next model below
it and continues the same turn: the transcript shows a line "cascade: <model>
failed (<error>); cools until HH:MM. Continuing on <next>.", and the new model
gets a message from aegis saying what failed. If the switch is refused, the
line says "could not switch to <next>" and the turn ends with the error; a Stop
or an interrupt while it switches ends the turn without continuing. An interrupt, a context-too-long or auth
error, and any error aegis does not recognize end the turn as before. When the
last model fails, the transcript says "cascade exhausted" and the turn ends with
the error. A session never climbs back up; the card's model line shows the
model and its place, such as "opencode/glm-5-free · 2 of 3".

A model that failed cools for its retry-after, or 10 minutes, and new sessions
skip it. Claude Code's usage limit cools every Claude model at once, since they
share one account. Cooling is kept in memory by the server: a restart clears it.
`/model` moves a session within its cascade; a model outside it turns falling
off for that session, and a handoff from it keeps that model. Codex errors never fall yet.

On the Settings page an agent's models are a list in the order they are
tried, each with its Intelligence Index or "unranked", and buttons to move it up or
down or remove it; the last model cannot be removed. "Add model" offers the
rest of the harness's models, and "Sort by ranking" puts them in the ranking's
order. Under the list a warning names the first pair out of that order, as
`aegis doctor` does, and a model that is cooling shows dimmed with "cooling
until HH:MM". Saving writes `models:` for two or more models and `model:` for
one, except that an agent the file already lists under `models:` stays a list.
Changing the agent's harness empties the list.

## Models

Every model pick (the new tab's model chip, the `/model` menu, "Add model" on
an agent in Settings) shows a short chip beside each model: its rank among the
models of its harness, its Intelligence Index, and its price per million tokens in
and out, such as "#1 · 57.6 · $4 / $20", or "free" when it costs nothing
through its harness. Rank follows the Intelligence Index, the Coding Index
breaking ties. A model with no Intelligence Index shows "unranked" and
its price. OpenCode's catalog gives its own prices; for Claude Code the price
is the ranking's list price.

The Settings page has a Models section that lists every model the installed
harnesses can run, one row each: name, harness, rank, Coding Index,
Intelligence Index ("unranked" when it has none), price in and out, and the agents that use it with its
place in each cascade, such as "free #2". Models in use are marked and come
first. Chips above the list filter it by harness and to free models only, and
sort it by any column. Under it is the ranking's date and its source.

An agent reads the same data with `models_list`: every model the installed
harnesses can run, with harness, rank, Coding and Intelligence Indexes, price
in and out, whether it is free, the agents that name it (`at` is its index in
the agent's models, 0 first) and the epoch it stops cooling, or
`"cooling_until": null` when it is not cooling. Models in use come first, then by Intelligence Index with the Coding Index breaking ties, unranked ones last. The
answer carries the ranking's date and its source,
[Artificial Analysis](https://artificialanalysis.ai).

## Claude Code tools aegis replaces

A Claude Code session starts without the Claude Code tools aegis has its own
version of, because each one's schema costs input tokens on every request
(about 5.9k a session): Agent, ListAgents, SendMessage, Workflow,
ScheduleWakeup, CronCreate, CronDelete, CronList, RemoteTrigger, Monitor,
TaskStop, PushNotification, TodoWrite, TaskCreate, TaskGet, TaskList and
TaskUpdate. The agent uses queues, session_spawn, peer_handoff, workflows,
schedules, monitors, toast and plan_update instead.

An agent whose work needs one of them, such as a skill that fans out
subagents with Agent, keeps it:

```yaml
agents:
  research: {harness: claude-code, model: opus, effort: high, permission: full, keep_harness_tools: [Agent]}
```

A name that is not on that list stops the agent with an error rather than
keeping nothing. Like the priming, the list is fixed when a session starts.

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

`recap:` with `agent:` names an agent that writes a short recap when a person
returns to a tab with a long unread stretch. The agent may use Claude Code,
OpenCode or Codex; the call is one-shot and read-only, with no tools or project
instructions. Without it, recaps are off. A cheap, fast agent is enough.

A recap is one quiet line in the transcript, the latest like the older ones,
with the model and its cost in its tooltip. It says what the session needs from
you only while the session waits on you. The sparkle in the transcript's
navigation pill asks for a fresh one.

## Side notes

`btw:` with `agent:` names an agent that answers `/btw` side questions. It reads
up to the last ten turns of the session, about 32,000
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

## Who owns a repo

`aegis usage` splits spend by the owner of each repo: the account or
organisation in its `origin` URL (`git@github.com:acme-co/atlas.git` is owned
by `acme-co`), or `local` when it has no remote. `usage:` names an owner for a
local repo, by the repo's directory name, and renames an owner for display:

```yaml
usage:
  owners:
    landing-page: acme-co      # a local-only repo, done for a client
  owner_names:
    acme-co: Acme              # how an owner is labelled
  subscriptions:
    a1b2: {name: claude-max, fee: 200, renews: 14}  # USD a month, billing day
```

`subscriptions` names a subscription by its account's hash, the part after the
colon in `aegis usage --split billing` (`claude:a1b2` is `a1b2`; Codex's plan is
`codex`), and gives it a `name` and a monthly `fee` in USD. With a fee,
`aegis usage --measure subscription` and the usage page share it over the work
that ran on it; without one, only the weekly limit used is shared. `renews`,
the billing day, is optional and kept for the record; the split does not use
it.

A repo's owner is read once and read again when its `.git/config` changes. A
`usage:` block with a wrong key or value leaves every default in force, and
`aegis doctor` names the mistake.

## Notices about the host

`notices:` sets when aegis tells agents that the host runs short (the
sessions page says what they are told). Every key is optional; these are the
defaults, in percent:

```yaml
notices:
  disk:   {on: 90, critical: 97, clear: 85}
  memory: {on: 90, critical: 95, clear: 85, psi_full: 5}
  cpu:    {on: 50, clear: 30}
  quota:  true
```

A notice starts at `on:`, is sent again at `critical:` and ends below
`clear:`. `psi_full:` also starts the memory notice when memory pressure
reaches it, whatever the percent. cpu has no critical. `false` in place of a
kind turns it off; announcements cannot be turned off. An `on:` given alone
moves `clear:` and `critical:` with it by the default gaps, so `disk: {on:
80}` reads as clear 75 and critical 87. A block with a wrong key, a number
outside 0 to 100, or levels out of order (clear below on, on at most
critical) leaves every default in force, and `aegis doctor` names the
mistake.

## Notices about an agent's own work

The same block sets what aegis tells an agent about its own work: its plan,
its monitors and the queue tasks it has out (the sessions page shows the
status block they make). Each is a source that declares its keys; these are
the defaults:

```yaml
notices:
  plan:     {stale_min: 10}
  monitors: {stall_min: 15, broken_checks: 3, overdue_min: 10, wake: true}
  queues:   true
  held:     true
  status:   {lines: 5}
```

`plan.stale_min` is the minutes of work on a plan left untouched before the
agent is warned. `monitors.stall_min` is the minutes a monitor's progress may
sit still (and at least three intervals), `monitors.broken_checks` how many intervals a
check may fail to run, and `monitors.overdue_min` how far past its ETA a monitor may
run, before its owner is warned; with `wake: true` the warning wakes an idle
owner. `held` warns a busy agent of each message held for its turn's end. `status.lines` caps the status block. Minutes may be fractions.
`false` in place of a source turns it off; `status: false` drops the status
block and keeps the warnings. A name no installed source declares is ignored,
and `aegis doctor` warns about it.

## Nudges

`nudges:` sets when aegis asks an agent that drifted where it stands:

```yaml
nudges:
  silent_s: 2     # after a turn that ended without turn_end; false: never
  plan_min: 15    # minutes of work on a plan left untouched, at a turn's end
```

A wrong value leaves both defaults in force, and `aegis doctor` names it.

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

Everything aegis starts is capped together too: sessions, terminals and the
browsers it drives, at the service's lowest memory limit less 512 MiB that
stays the server's. When they fill the host under their own caps, the kernel
kills the largest process among them, and the server keeps answering. Each
of them may run at most 4096 tasks (processes and threads), and all of them
together leave the server 512, so a fork bomb dies in its own session. The
server also runs and reads the disk before any of them when the host is
busy; an idle CPU or disk is still theirs. None of this has a key.

What an agent starts outside aegis's processes is not covered: a container
from `docker run` lives under dockerd, and a unit from `systemd-run` under
systemd. Run heavy work in the session itself, where the caps hold it.

The cap needs a cgroup per session, which aegis makes only when it runs as a
systemd service with `Delegate=yes`. Anywhere else, every session shares the
server's cgroup, as before, and the key has no effect. A value that is not a
size leaves the default in force, and `aegis doctor` names the mistake.

## Parking idle sessions

`park:` maps a harness to the seconds an idle session of it must be safe to
end before aegis ends its process to free its memory. The session is then
asleep with the reason `idle`, and the next message wakes it. The sessions
page says what makes a session safe to end.

```yaml
park:
  claude-code: 1200   # 20 minutes
```

A harness the block does not name never parks, and without the block nothing
does. A block with a harness aegis does not know, or a value that is not a
positive whole number of seconds, parks nothing, and `aegis doctor` names the
mistake. Only an admin can change it, in the file or in the Parking section
of the Settings page, which takes minutes and which members do not see.

Only Claude Code's and Cursor's resumes have been measured — Cursor's runs
`session/load` on the same session id. Codex and OpenCode sessions park when
named, and `aegis doctor` warns that how they resume is unmeasured.

## Untested harness versions

Each aegis release names, per harness, the lowest and highest version it was
tested with. When the installed one is outside that range, aegis says so,
names the binary it found and gives the command that installs the newest
tested version. `harness_versions:` decides what happens then, and every
field has a default:

```yaml
harness_versions:
  person: ask       # ask | warn | off
  agent: warn       # warn | off
  startup: warn     # warn | off
  accepted:         # versions allowed for good, per harness
    opencode: ["2.0.33"]
```

- `person:` is for a session a person starts: `ask` (the default) pauses and
  asks whether to start once, allow that version for good, or cancel; `warn`
  starts it with a notice in its transcript; `off` says nothing.
- `agent:` is for a session started by an agent, a queue, a schedule, a
  workflow, or the wake of a parked session: `warn` starts it with the notice,
  and `off` says nothing. It cannot be `ask`, because nobody is there to answer.
- `startup:` is for `aegis serve`, which checks the harnesses its agents name
  when it starts and logs one line per version outside the range. It never
  delays or stops the server.
- `accepted:` lists versions a person allowed for good. "Always allow" in the
  dialog adds one; the next update asks again.

A block with a wrong key or value leaves every default in force, and
`aegis doctor` names the mistake. Only an admin can change it, in the file or
in the Untested versions section of the Settings page.

## Disks

The Host rows of the Fleet band and of a session's sidebar list every disk
the server mounts, one row each, named by its mount point when there is more
than one: the block devices in the mount table, less loop devices (snaps),
squashfs images, and the boot and EFI partitions. A filesystem
mounted twice (a bind mount, a btrfs subvolume) is listed once. The disk that
holds aegis is always listed. The sidebar's `disk` tile shows the fullest.

`host:` hides mount points, and adds ones that are not block devices, such as
a ZFS dataset or an NFS share:

```yaml
host:
  hide_mounts: [/srv/scratch]
  add_mounts: [/tank]
```

A block with a wrong key, or a mount point that is not an absolute path,
leaves both lists empty, and `aegis doctor` names the mistake. Only an admin
can change it, in the file or in the Host section of the Settings page.

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

Settings → Workflows edits it: a row for the total and one per agent; blank
keeps the default.

## Search embeddings

`knowledge:` turns on the optional embeddings for searching skills and
memories (help_read knowledge). Off, the default, ranks by keywords alone
(BM25). With a fastembed model named, search ranks by fusing the keyword
rank with the model's rank, so a Spanish message can find an English skill
that shares no word with it.

```yaml
knowledge:
  embeddings: minishlab/potion-multilingual-128M   # or off, or another fastembed model
```

It needs the extra: `uv tool install 'aegis-harness[embeddings]'`. The
model downloads on first use and stays in memory, about 1.2 GB for this one;
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` is the other
model worth it, about 1.1 GB and slower. Keep it off on a small host. The key
is read when aegis starts, so a change needs a restart. Without the extra,
`aegis doctor` warns and search goes on by keywords alone.

Settings → Search embeddings edits it.

`knowledge.know_how`, true by default, tells a session about a repository's
`know-how/` docs when it reaches that repository (help_read knowledge). Set it to
false to turn that off. It is read when aegis starts.

## Exit through a link

`exit_via:` names a trusted link: what aegis starts reaches the internet from
that server's address. `exit:` on an agent names another link, or `off` for the
machine's own route. See [links](links#exit-through-a-trusted-link).

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

The Settings page edits the same file as a form, every key on this page
included: agents with their chips, models, priming and kept tools, which one
is the default, the queues, recaps and side notes, terminals, the memory
caps, the context thresholds, nudges, notices, parking, untested versions,
workflows, the search embeddings and the network readings. A field left blank is left out of the
file, so its default holds; the field shows the default. It keeps the file's
comments and key order, and keys it does not read, and refuses to save over a
change made on disk since you opened it. Only an admin can save.

`aegis init` writes a first file from the harnesses installed on this machine,
proposing agents and queues and asking about each (`aegis init --yes` takes
every proposal). With Claude Code it proposes `opus`. With OpenCode it
proposes `free`, a cascade of every free model in its catalog that can run an
agent, best first by the ranking and unranked ones last; a cascade is asked
as one comma-separated list. A paid model never joins the cascade; a
catalog with no free model gets its best model alone. With both,
`opus` is the default, the `general` queue runs it and a `light` queue runs
`free` for reviews and research; with OpenCode alone, `free` is the default
and runs `general`. The Settings page's Set up offer, on a server with no
file, makes the same proposal.
It refuses to overwrite an existing `.aegis.yaml`.

## Suggested changes, applied from a card

The file is never written by a session. `config_suggest` puts a card with
the whole new text and its diff in the transcript; Apply writes exactly that
text, Not now writes nothing, and either way the session is woken. Only an
admin applies, as only an admin saves Settings. A file changed since makes
the card stale, and nothing is written. `/setup` uses it (see the page
setup).

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

The server also runs these checks on its own: 10 seconds after it
starts, whenever `.aegis.yaml` changes, and every 6 hours, since a harness can
be upgraded outside aegis. While the last run found a warning or an error, an
admin's new tab shows a row under the chips: "This server's setup has 2 errors
and 1 warning", with a `/doctor` button that starts a session to fix them with
an agent, and a chevron that unfolds each finding. Other people never see it.
