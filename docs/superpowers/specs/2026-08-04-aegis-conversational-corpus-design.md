---
title: The conversational corpus — provenance, recall, and export over aegis's own ledger
date: 2026-08-04
status: design
---

# The conversational corpus

aegis has kept a durable, per-session transcript since May. Nothing can ask it
anything.

That gap surfaced from the other side. Alex asked whether the accumulated
history could be mined for the mistakes we keep repeating — and the mining ran
against `~/.claude/projects/`, Claude Code's own rotating transcripts, because
nobody involved knew aegis had a better copy. It does: 297 sessions back to
2026-05-22 against Claude Code's 218 back to 2026-07-03, typed rather than
reverse-engineered, and permanent rather than swept every 30 days
(`archive_old_logs` in `state/session_log.py` gzips in place — *"a transcript is
the only copy of a conversation, so this compresses rather than deletes"*).

Two use cases want that corpus, and they are the same object underneath:

- **Mining** — "what do we keep getting wrong?" Wants the corpus sharded for
  offline fan-out.
- **Recall** — "remember we talked about this a few weeks ago?" Wants it
  indexed for a low-latency point query.

Both need the identical middle step: turning a raw event log into *exchanges*.
Build that once and this is one feature, not two.

## What we measured first

Everything below is grounded in numbers taken on 2026-08-04 against the live
state dir, not in what the code looked like it should do.

**The ledger was blind to the operator.** `UserMessage` is claude's
`--replay-user-messages` echo (`drivers/claude.py:241`), and that flag landed
only weeks ago:

| month | sessions | with ≥1 `UserMessage` |
|---|---|---|
| 2026-05 | 54 | 0 |
| 2026-06 | 73 | 0 |
| 2026-07 | 154 | 5 |
| 2026-08 | 16 | 15 |

337 `UserMessage` events against 42,265 `AssistantText`. **93% of sessions
recorded the agent talking to itself.** Complete going forward; blind behind.

**A hand-run recall query found the shape, and four defects.** A prototype
extractor plus IDF ranking, against *"how is ainbox images pushed to syalia
registry"*:

- Plain lexical is useless — 87 sessions match `registry.syalia.dev`, 162 match
  `digest`. Ranked exchanges surfaced the right cluster on the first try.
- **Six of the eight top hits were peer-agent handoffs rendered as if the
  operator had said them.** Ranking amplifies provenance contamination rather
  than tolerating it.
- The live session self-matched at rank 7.
- A regex grep over 730 MB blew a 120-second timeout. The index is not a nicety.

**And the answer was already in `know-how/`.** `cutting-a-release.md` documents
the procedure precisely. History gave the *symptom* (stale pins across forge,
zion and the live site — `versions.env` in 71 sessions, `engine-pin-check` in
21); the doc had the *procedure*. Recall's job is not to replace know-how. It is
to surface pain not yet promoted to a doc, and to answer *when and why did we
decide this*.

## The backfill, already landed

Before designing anything we stopped the loss, because Claude Code's transcripts
were the only record of the operator's turns before August and they delete at 30
days.

- 1,492 transcripts archived to `state/claude-import/`, 930 MB → 385 MB, verified
  by decompressing and parsing every one: **0 corrupt, 229,859 lines**.
- 212 provenance-tagged sidecars written to `state/backfill/`, joined via
  `SystemInit.session_id` ↔ the transcript filename.

| | before | after |
|---|---|---|
| operator turns in the ledger | 346 | **3,232** |
| sessions with zero operator turns | 277/299 (93%) | **86/299 (29%)** |

93 interrupts recovered. May and June are gone permanently — their transcripts
were already swept. The remaining 86 blind sessions are those.

No existing log was modified; 299 checksums taken before and after, the only two
differing being live sessions appending during the window.

## Architecture

```
session logs ─┐
              ├─→ extractor ─┬─→ beaver index ─→ aegis_recall      (MCP)
   sidecars ──┘              └─→ shard writer  ─→ aegis history export (CLI)
```

### The exchange

An **exchange** is one operator turn plus the agent's response arc up to the
next one. It is the retrieval unit because the alternatives are both wrong: a
session is ~3 MB and too coarse to return, a single `ToolUse` is meaningless
alone.

| field | source | why |
|---|---|---|
| `operator_text`, `assistant_text` | `UserMessage`, `AssistantText` | the searchable content |
| `handle`, `log_id`, `ts_start/end` | `SessionMeta`, `aegis_ts` | answers "which conversation, when" |
| `cwd`, `profile`, `provider`, `host` | `SessionMeta` | scoping and filtering |
| `files_touched` | `ToolUse.input.file_path` | *"when were we working on the geocoder"* is a path facet, not a semantic query |
| `tools_used` | `ToolUse.name` | *"the session where we used mosaico"* |
| `friction[]` | `Interrupted`, `TurnAborted` | the highest-signal markers |

Tool *results* are excluded — roughly 60% of corpus bytes and near-zero
retrieval signal.

**Boundary rule.** `source ∈ {operator, agent}` starts a new exchange; every
other source attaches to the current one. Handoffs carry genuine new intent;
monitor wakes and queue callbacks are continuations, and letting them split
would fragment one task into a dozen fake exchanges. This is a tunable constant
in the extractor, not a buried assumption — it shapes every downstream result
and must stay cheap to revisit.

The extractor is **pure** — events in, exchanges out, no I/O — following
`btw/window.py`, which is pure for exactly this reason: it is the piece worth
testing hard.

### Provenance

`UserMessage(text)` gains `source` and `sender`:

```
source ∈ {operator, queue, agent, monitor, canvas, term,
          workflow, loop, reminder, harness, unknown}
```

The subtlety that decides the design: the recorded event is claude's *echo*, not
aegis's injection, so at parse time aegis does not directly know what it sent.

**A pending-send table resolves it.** When aegis injects a turn (handoff, queue
callback, monitor wake, canvas notification) it records `text_hash → (source,
sender)`. When the echo arrives, look up by hash — exact hit gives true
provenance, a miss means the operator typed it. No ordering assumptions, no
correlation heuristics.

Header-sniffing (`> from queue:…`) remains the **import-only** path. It is what
produced the 212 sidecars and it works, but an operator can legitimately type a
line beginning `> from`, so it is not sound for the live path.

Records without the field decode as `unknown` via a defaulted dataclass field.

Observed distribution across the backfill, which is what the live ledger is
currently unable to distinguish:

| source | turns |
|---|---|
| operator | 2,886 |
| harness | 918 |
| monitor | 421 |
| agent | 357 |
| loop | 66 |
| canvas | 36 |

### Friction events

Two additive event types:

- `Interrupted(at, during_tool=None)` — emitted where aegis owns the keybinding.
- `TurnAborted(reason, detail)`, `reason ∈ {transport, quota, session_limit,
  harness_error}`.

Today both are invisible as structure: session-limit messages and
`API Error: ENOTIMP` sit in the corpus as ordinary assistant prose. The driver
sees them at stream level, which is where they should be classified.

### Index

**beaver** — the house default, pure-Python, and it supplies FTS now and vectors
later from one dependency. Two known traps are designed around from the start:
its FTS5 layer rejects punctuation in queries, so tokens are sanitized on the
way in; and it has no reranker, so hybrid fusion is roll-your-own RRF when
vectors arrive.

Incremental indexing uses a `(log_id → byte_offset, size)` watermark. The logs
are append-only, so this is exact rather than heuristic: re-read only files that
grew. Triggered on `SessionClosed`, plus `aegis history index --rebuild`.

### Surfaces

```
aegis_recall(query, since?, until?, cwd?, all_projects=False, handle?, limit=5)
  → [{exchange_id, handle, ts, cwd, score,
      operator_snippet, assistant_snippet, files}]

aegis_recall_expand(exchange_id, before=1, after=1)
  → full text of that exchange and its neighbours
```

Two steps, deliberately. A single tool that returned matches in full would burn
context on every use; ranked snippets are cheap and expansion is opt-in. Recall
returns ≤ ~2 KB.

Defaults: scoped to the caller's `cwd` with `all_projects` as the escape hatch,
**current session excluded**, limit 5. No natural-language date parsing in
aegis — the agent turns "a few weeks ago" into `since="30d"` before it calls.

```
aegis history export [--since 60d] [--only operator]
                     [--shard-bytes 90k] [--out DIR] [--format corpus|jsonl]
aegis history import-claude [--src ~/.claude/projects] [--apply]
aegis history index [--rebuild]
```

`--format corpus` emits the sharded markdown batches that fed 15 parallel
readers successfully on 2026-08-04.

`import-claude` promotes the backfill to a first-class path. Sidecars live in
`state/backfill/`, **never** in `state/sessions/` — `repair.py:61`,
`session_log.py:160` and `history.py:203` each glob `sessions/*.jsonl`, and a
sidecar there would be read back as a session log. The extractor merges the two
by timestamp **at read time**, so originals are never rewritten.

### `SessionMeta` gains `host`

Remote execution hosts landed in `f6e2093`; `SessionMeta` carries `handle`,
`profile`, `provider`, `cwd`, `created_at`, `origin`, `preview` and no host. A
unified corpus needs the field now rather than as a migration later.

## Slices

| slice | scope | rationale |
|---|---|---|
| **VS1** | extractor + beaver index + `aegis_recall` / `aegis_recall_expand` | the headline capability; the extractor is prototype-validated and 3,232 backfilled operator turns are already on disk to test against |
| **VS2** | provenance at source (pending-send table) + `Interrupted` / `TurnAborted` + `SessionMeta.host` | makes the live ledger self-sufficient, so import is only ever needed for history |
| **VS3** | `aegis history export` | a thin layer on a proven extractor |
| **VS4** | embeddings + RRF fusion | only once we can feel what FTS misses |

VS1 reads provenance by deriving it from headers, exactly as the import does.
That is good enough to rank correctly over the backfilled corpus and it keeps
VS1 free of any schema change.

## Testing

The extractor is pure, so golden-file tests over synthetic event streams plus
one real log carry most of the weight. Three tests earn their keep beyond that,
each written so that it can actually fail:

- **Provenance mutation.** Flip a `> from` header on a fixture and assert the
  classification changes. A classifier that returned `operator` unconditionally
  would pass a naive assertion, and that failure mode is precisely what
  contaminated the first mining run.
- **Watermark.** Append to one log, re-index, assert only that log was re-read.
  Without this, "incremental" silently degrades to a full rebuild and nothing
  visible changes.
- **Punctuation query.** `"how is ainbox pushed to registry.syalia.dev?"` must
  not throw. beaver's FTS5 rejects punctuation, and this is the exact query
  shape a user types.

Plus an end-to-end assertion that recall excludes the calling session — the
prototype ranked itself 7th, and that is invisible unless asserted.

## Risks and accepted trade-offs

**beaver becomes an aegis dependency.** Pure-Python and house-standard, but it
is a new runtime dependency on a tool that ships to remote hosts.

**The index makes 730 MB searchable that previously nobody grepped.** It
contains every token and secret ever pasted into a session. Accepted for v1:
local-only, never network-exposed, no redaction pass. Revisit before any
surface that leaves the machine.

**Index size** is expected in the 100–200 MB range against the current corpus.

**May and June operator turns are unrecoverable.** Noted so nobody later
mistakes the gap for a bug in the extractor.
