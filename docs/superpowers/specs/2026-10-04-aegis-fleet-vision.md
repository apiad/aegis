# One operator, many machines: aegis as a fleet — vision

> **Status:** vision doc, captured in conversation with Alex on 2026-10-04
> after watching Theo's "If you have a Claude sub, watch this" and measuring
> how aegis is actually used on zion. Not a plan and not a spec. It writes
> down the destination so that issues and specs can point at it.

## The seed

Theo (t3.gg) runs ten or more agent threads at once across a laptop, two
Macs, three Linux boxes and a cloud server, and sees all of them in one
sidebar in T3 Code, on desktop and on his phone. He picks the machine when he
starts a thread, or lets T3 Code balance threads across machines. Each thread
starts in its own worktree. A thread is a task rather than a history: it
fades while it works, asks for attention when it is done or needs input,
and disappears once he settles it or its PR merges. He prompts for the whole
path, from the problem to a merged PR, and expects about half his threads to
close without him reading their last message. Then he goes to bed and the
home box keeps working.

aegis on zion, measured the same day over the 56 days to 2026-10-04:

- In 86% of wall-clock minutes no agent was working. The mean number of
  agents working at once was 0.34, and weekly means ranged from 0.23 to 0.66.
  Peaks of 15 to 33 were short fan-outs.
- The median turn took 2.3 minutes and the p90 11.6. The operator steps
  back in every few minutes; Theo steps back in when a PR is ready.
- 11.9% of busy minutes fell between 00:00 and 07:00, and Saturday plus
  Sunday held 10.6% of turns.
- The Claude weekly pool ended at 33%, 74%, 33% and about 123% in the four
  weeks from 2026-09-04 (calibration in the workspace's
  `vault/Atlas/Architecture/2026-09-30-opencode-offload-sizing.md`). Three
  weeks left a quarter to two thirds of it unspent, and one ran out.

The script behind these figures is
`.playground/theo-claude-sub/concurrency.py` in the workspace (not
committed). It covers sessions aegis ran and nothing else.

The gap is not a missing model or a missing quota. aegis's unit today is *a
daemon and its tabs*. T3 Code's unit is *the operator and their tasks,
wherever those run*. This document is about moving aegis to the second.

## What already exists

Most of the parts are built, but each one stops at a single machine:

| piece | what it does today | where it stops |
|---|---|---|
| the daemon | owns sessions, transcripts, inboxes, queues on one machine | sessions belong to that machine and cannot leave it |
| `hosts:` | the local daemon runs a harness on another box over SSH | the harness dies when the link drops; durability is out of scope by design |
| `remotes:` / remote plane | two daemons deliver callbacks to each other | peers on different daemons cannot address each other, and handles are not unique across machines (#86) |
| `aegis web` / `aegis attach` | a browser or terminal attaches to a remote daemon | one daemon per view |
| fleet dashboard (F10, `aegis dash`) | every session on one daemon, with recaps, gauges and quota | one daemon |
| quota service | readings shared across processes on one machine | one machine, and the Claude usage endpoint tolerates about one poller |
| AFK coordinator | works a GitHub Project of prompt cards | runs on one daemon; slices 4 to 7 unbuilt |

The open issues that already point this way: #86 (cross-daemon
addressing), #119 (`/transfer` a session to another remote), #120 and #121
(sessions keyed by a mutable handle), #61 (file affordances on an SSH
host), #63 (shared terminals are local only), #89 (voice records from the
daemon host's microphone).

## Idea 1: the fleet, not the daemon, is what a client attaches to

A client (TUI, web, phone) attaches to a fleet: the set of daemons the
operator owns. It lists every session on every daemon in one view. Each row
names its machine, and rows are grouped by repository, identified by the
git origin URL rather than the local path, because the same repo sits at a
different path on each box.

Each daemon stays the single owner and single writer of its own sessions.
The fleet is a federation of owners, not a cluster: no shared database, no
consensus, no leader. A client merges what the daemons report, and actions
on a session go to the daemon that owns it.

This needs a session identity that is immutable and unique across machines
(#121, #86). A handle is a display name, and two machines will both mint a
`lucid-knuth`.

## Idea 2: where a session runs is chosen at launch, or left to aegis

Starting a session takes a machine as one more parameter, beside the agent
profile and the repo. The operator can name one, or let aegis place it. A
placement policy weighs:

- whether the machine has the repo, or can clone it;
- load, which the fleet dashboard already measures (CPU, RAM, disk);
- whether the machine stays awake: a laptop is a bad home for a task
  that runs through the night;
- constraints the operator declares per profile, for example that Claude
  sessions run only on hosts behind a residential connection (see Idea 7).

Two kinds of remote work need to stay distinct. With `hosts:` the session
lives on the local daemon and only the harness runs elsewhere; it is
interactive and dies with the link. A session owned by the remote daemon
survives the laptop closing. Placement in this vision means the second
kind. `hosts:` remains the right tool for "drive this one box from here".

## Idea 3: work moves between machines

Closing the laptop should not kill or freeze the work in it. `/transfer`
(#119) moves an in-flight session to another daemon: the transcript and
session state travel, the branch is pushed so the other clone can check it
out, and the receiving daemon resumes the harness on its side. The branch is
the unit that crosses machines, because worktrees do not.

The everyday case is a single command at the end of the day: move every
working session off this laptop to the home box.

## Idea 4: sessions are tasks, and the view is an inbox

A session is in one of a few states that matter to the operator:
working, needs you (asking a question, or blocked), done and unread, and
settled. The default view shows what needs the operator. Working sessions
recede. A finished session is settled, either by hand, automatically when
its PR merges, or after some days untouched. Snooze brings one back later.
The recaps the fleet dashboard already generates are what the operator
reads instead of the transcript (#108, #111).

Starting work must not cost the operator their place: one keystroke sends a
prompt to a new session in a fresh worktree, on a chosen or placed machine,
and leaves the operator where they were.

## Idea 5: a session runs until its PR can merge

The common prompt should carry the task all the way: understand the
problem, implement, open the PR, watch CI, have a second model review,
address the review, and merge if the conditions the operator stated hold.
Theo estimates that 80 to 90% of his tokens go to verification. aegis has
every piece (workflows, monitors, queues, worktrees, peers on other
harnesses for review). What is missing is the packaged loop, so that "fix
this and babysit the PR" is one instruction rather than a conversation.

This is the idea that turns a 2-minute median turn into an hour of
unattended work, which is what makes Ideas 1 to 4 worth having.

## Idea 6: quota is a fleet resource that aegis paces

Every subscription has windows that reset, and an unspent window is lost.
aegis already reads the quota and projects where the window ends. The next
step is to act on the projection:

- **Behind pace** (percent used below percent of the window elapsed): the
  coordinator pulls from a backlog of work that is worth doing whenever there
  is spare capacity. Issue triage, PR review, stale-branch sweeps, mutation
  runs, "what should I revive" passes.
- **Ahead of pace**: route workers to a cheaper rail (OpenCode Go), and
  keep the subscription for the main sessions and reviewers.

The reading is a fleet-wide fact. One poller per account per fleet, shared
with every daemon, because the Claude usage endpoint rate-limits a second
consumer.

## Idea 7: more accounts and custom egress, opt-in and at the user's risk

Anthropic's Claude Code terms
([legal and compliance](https://code.claude.com/docs/en/legal-and-compliance),
read 2026-10-04) allow an end user to sign in to the unmodified Claude Code
binary with their own subscription, including where a platform hosts it.
They forbid developers to collect, store or intermediate Claude.ai
credentials or session tokens, or to route requests through Pro or Max
credentials on users' behalf. They also say the advertised limits assume
ordinary, individual usage.

aegis drives the real binary and is on the allowed side. It stays there by
following three rules:

1. aegis never holds, relays or proxies a subscription token. Sign-in always
   completes through the harness's own flow.
2. An account is a profile with its own config directory (for Claude Code,
   `CLAUDE_CONFIG_DIR`), signed in through `/login` inside that profile.
   Placement can then pick the account with headroom that resets soonest,
   and a session never changes account mid-conversation, because the prompt
   cache belongs to the account.
3. A user who wants a proxy can point a profile's base URL at their own.
   aegis supports the setting and neither ships nor documents a proxy. The
   multi-account mode is off by default, and turning it on shows the
   "ordinary, individual usage" sentence.

Where traffic leaves from is part of placement. Theo's ban reports come from
datacenter IPs and from one account active on several machines at once. A
fleet that keeps Claude sessions on a home box avoids both without any proxy.

## Idea 8: a machine joins the fleet by an agent following a recipe

Adding a box should be one command that hands an agent an SSH target and a
recipe: install the harnesses and aegis, run the daemon as a service, join
the tailnet, register with the fleet. The recipe is a Markdown file the
operator owns and edits, in the spirit of Theo's fleet-management repo. The
workspace already has the pieces for its own machines (a fresh-host bootstrap
skill, headscale).

## Non-goals

- No hosted service. The fleet is the operator's own machines, talking over
  their own network.
- No token relay, no shared credential store, no billing layer.
- No cluster. Each daemon owns its sessions; there is no distributed state
  to keep consistent.
- No scheduling of compute aegis does not own. Placement chooses among the
  operator's daemons, not cloud instances it would have to create.

## Open questions

- What is a "remote" once fleets exist? `hosts:`, `remotes:` and attached
  views are three mechanisms with three meanings. Do two of them collapse
  into membership in a fleet?
- How do daemons find and trust each other? The tailnet gives
  reachability; is membership in it enough authority, or does each daemon
  need a fleet key?
- What travels in a transfer, and what is rebuilt? A Claude session resumes
  from its own transcript file, so it either moves with the session or the
  session restarts from aegis's ledger.
- Is a laptop a full daemon or a client with a small local daemon? It owns
  interactive work and should own nothing that must outlive the lid.
- File claims are scoped per host today. Across a fleet the shared state is
  the git remote, so is a claim on a branch the right unit?

## Sequencing (rough)

A hint, not a plan, in order of dependency:

1. Immutable, fleet-unique session identity (#121, #86). Everything else
   names sessions.
2. A client that lists sessions from several daemons (Idea 1), read-only at
   first.
3. Task states, settle and background launch (Idea 4), useful on one
   machine before it is useful on several.
4. The run-to-merge loop (Idea 5).
5. Launch on a chosen machine, then placement (Idea 2).
6. Transfer (Idea 3, #119).
7. Fleet-wide quota pacing (Idea 6), then opt-in accounts (Idea 7).
8. Joining a machine by recipe (Idea 8).

Steps 3 and 4 pay off on zion alone, and they are what raises the 0.34
mean before any second machine is involved.

## Origin

Conversation with Alex on 2026-10-04 in the aegis session
`aegis-tokenmaxxing`, after reviewing Theo's video
<https://www.youtube.com/watch?v=D8PikZ1KhUo> (transcript in the workspace
at `.playground/theo-claude-sub/transcript.md`, not committed) against
`aegis usage` and the session logs. Alex asked for the overall direction
only: no spec, no plan.
