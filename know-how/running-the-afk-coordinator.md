---
when: standing up, debugging or turning off the AFK coordinator against a real GitHub Project; a card is stuck in Todo, Running or Blocked and the reason is not on the card
---

# Running the AFK coordinator

Written from the first real run, 2026-09-25/26. Everything here was observed,
not designed — the design is in
`docs/superpowers/specs/2026-09-25-afk-coordinator-design.md` and the user-facing
reference is `docs/afk.md`.

## Build the board

`Status` needs seven options and the default board gives you three, so rewrite
the field rather than adding to it:

```bash
SF=$(gh project field-list <N> --owner <owner> --format json \
      --jq '.fields[]|select(.name=="Status")|.id')
gh api graphql -f query='
mutation($f:ID!){ updateProjectV2Field(input:{fieldId:$f, singleSelectOptions:[
  {name:"Todo",color:GRAY,description:"eligible"},
  {name:"Waiting",color:BLUE,description:"deferred by the coordinator"},
  {name:"Running",color:YELLOW,description:"a worker is on it"},
  {name:"Needs review",color:GREEN,description:"gate green; your turn"},
  {name:"Blocked",color:ORANGE,description:"needs a person"},
  {name:"Failed",color:RED,description:"gate red"},
  {name:"Done",color:PURPLE,description:"you closed it"}
]}){projectV2Field{... on ProjectV2SingleSelectField{options{name}}}}}' -F f="$SF"
```

**Do not try to create a field called `Repo`.** GitHub reserves that name and
`Repository`, and `createProjectV2Field` refuses with "Name cannot have a
reserved value". The default is `Target repo`; `Checkout` also works. Its
options are the repo whitelist, so add one option per repo you are willing to
have a worker write in.

`Priority`, `Progress` and `Deadline` are ordinary `createProjectV2Field` calls.

An org-owned project can hold issues from a personal repo, so a company board
can carry `apiad/*` cards.

## Never point it at a checkout anyone else uses

`repo_root` must contain clones nothing else touches. A worker creates branches
and commits; the preflight refuses a dirty tree; and both of those fight a
checkout that a human or a peer agent is editing. Clone to a scratch path:

```bash
mkdir -p ~/.cache/afk/repos && git clone <url> ~/.cache/afk/repos/<name>
```

The first run of this landed on a repo whose default branch was being committed
to by three other agents. It refused every card with `working tree is dirty`,
naming files nobody involved had edited.

## Run it under something that restarts it

`aegis serve` reaps itself after `AEGIS_IDLE_TIMEOUT` seconds (default 1800) of
idleness. Armed schedules now count as work, so a coordinator daemon is no
longer reaped for sitting quiet — but that was a fix, and the reaper is not the
only way a daemon dies. On a server, systemd with `Restart=always`. Locally,
expect to restart it by hand and check it is alive before believing a quiet
board.

## Why a card is not moving

Read these in order. The first three cost no API calls.

```bash
# 1. is the daemon even up?
fuser <root>/.aegis/state/daemon.sock 2>/dev/null

# 2. is the schedule firing, and what did it say?
tail -3 <root>/.aegis/state/schedules/afk.jsonl | python3 -m json.tool

# 3. the tick's own narration, including why it started nothing
grep 'workflow:afk' <serve log> | tail -5

# 4. only now, ask the board
gh project item-list <N> --owner <owner> --format json --limit 20
```

Failures seen in the first run, and what each looked like:

| On the card / in the log | What it was |
|---|---|
| `unknown workflow: 'afk'. Available: []` | `workflows:` in `.aegis.yaml` was not being registered at all |
| `UNKNOWN_CHAR ("n") at [1, 1]` from GitHub | the GraphQL query was being shell-quoted with double quotes, losing newlines and `$vars` |
| `quota unread; not starting new work`, forever | the usage endpoint was 429ing; see below |
| card back in `Todo` with a live worker still running | the progress tick had eaten the card's marker, so reap could not find the task |
| `working tree is dirty`, naming files nobody edited | a worker's own `make check` reformats `src/`, caught mid-write |
| `daemon idle for 1800s; exiting`, then silence | the daemon reaped itself; the schedule log just ends |

## The quota endpoint is effectively single-consumer

`api.anthropic.com/api/oauth/usage` 429s readily. `QuotaService` polls it every
60 seconds for the TUI status bar, refreshes at a 10-second floor at turn end,
and hands off for 300 seconds after a 429 — and `force=True` skips the floor but
**not** the cooldown.

So: a headless `aegis serve` runs no quota poller, and the coordinator at `*/10`
is six calls an hour. On a laptop with the TUI open you are adding to ~60
calls/hour, and **probing it yourself while debugging steals the allowance the
coordinator needs at its next tick.** If the gate is stuck on `quota unread`,
stop running `aegis usage quota` and read the tick's log instead.

## Watch the board sparingly

GitHub's limit is 5000/hour **shared across every tool and agent on the
machine**, and it is easy to exhaust by monitoring rather than by working. The
first run burned it with a 45-second poll whose progress condition called the
same reader a second time: ~160 board reads an hour to watch one card.

One call per poll, cached for anything else that wants the value:

```bash
# done condition: one read, caches the answer
S=$(gh project item-list <N> --owner <owner> --format json --limit 20 | ...)
echo "$S" > /tmp/afk-status.txt
case "$S" in "Needs review"|Failed|Blocked) exit 0;; *) exit 1;; esac
# progress condition: reads the file, no API call
```

Check the budget before concluding anything about the board:
`gh api rate_limit --jq .resources`.

## Turning it off

Disable the schedules; do not kill the daemon, which only stops it until
something restarts it.

```bash
cd <root> && aegis schedule disable afk
cd <root> && aegis schedule disable afk-progress
```

`aegis schedule enable <name>` puts it back, and `aegis schedule list` shows the
armed state and the fire count. `aegis schedule show <name>` adds the last ten
fires, which is the same data as the JSONL above but easier to read.

There is also `aegis schedule run <name>`, which invokes the workflow directly
with no MCP and no queue. It is useful for checking that a tick reads the board
and decides sensibly, and it is **not** a way to test a full card: a worker
enqueued by that path dies with the process, so the card is left `Running` with
a task id nothing will ever reap. Use it to read, not to dispatch.

To stop the daemon as well, `aegis kill` from the root — it names the pid it
stopped and leaves other roots' daemons alone. Do not hunt for it with
`pkill -f`: the pattern matches the shell running it.
