---
title: Session Persistence
date: 2026-05-21
status: draft
---

# Session Persistence

## Goal

Make the aegis workspace durable across process exits. Running `aegis`
in a project reopens the tab layout from the previous session: same
handles, same profiles, same order, same active tab — each tab a
genuinely resumed agent conversation with the model's own memory
intact.

## The hard rule

**Resume means resume.** A tab is restored only if the underlying
driver supports session resumption *and* the prior session is still
loadable. If we can't restore the model's memory, we don't open the
tab — we never replay a transcript at a fresh process and call it
"resumed."

Cold-transcript revival (read the prior turns as a primer, no model
memory) is a different feature with a different verb. Out of scope
here; will land later as `/continue from <transcript>` (handoff-style
prime).

## CLI shape

| Invocation | Behavior |
|---|---|
| `aegis` | Resume if `.aegis/state/workspace.json` exists, else fresh. Silent on first-ever run. |
| `aegis --clean` | Ignore `workspace.json` and start fresh. Don't delete the file — first tab change overwrites it. |

There is no `--resume` flag. Resume is the default verb; `--clean` is
the explicit override.

## Persisted state

Two files, both under `.aegis/state/` (project-rooted, gitignored —
same substrate as queues/inboxes):

```
.aegis/state/
  workspace.json              # tab roster
  sessions/<handle>.jsonl     # per-tab event stream
```

### `workspace.json`

The tab roster. Single object:

```json
{
  "version": 1,
  "saved_at": "2026-05-21T17:45:00Z",
  "active_handle": "lucid-knuth",
  "tabs": [
    {
      "handle": "lucid-knuth",
      "profile": "default",
      "order": 0,
      "provider": "claude-code",
      "session_id": "01HK...",
      "created_at": "2026-05-21T14:00:00Z"
    },
    {
      "handle": "wry-hopper",
      "profile": "fast",
      "order": 1,
      "provider": "gemini",
      "session_id": null,
      "created_at": "2026-05-21T15:30:00Z"
    }
  ]
}
```

**Write cadence**: rewritten on every tab change — tab open, close,
reorder, activate. Costs ~ms; crash-survivable; single source of
truth. No separate shutdown write.

**Worker tabs** (spawned by the queue substrate) are not written into
`workspace.json`. They're done business; on next start the queue
substrate replays its JSONL log as usual and marks any in-flight task
`failed:interrupted`. Resume covers only foreground (Alex-driven)
tabs.

### `sessions/<handle>.jsonl`

Append-only stream of aegis `Event` records for that tab, written
live as events arrive. One event per line: a serialized form of the
`Event` dataclass union from `aegis.events` (type tag + fields),
wrapped with `aegis_ts` and `v` (schema version) — same envelope as
the queue/inbox JSONL.

Used for **local transcript redraw** when a tab is reopened — the
model gets its own memory back through `--resume`; the JSONL is only
how aegis paints the screen. Protocol-agnostic by construction: every
driver maps wire payloads to `Event` objects, so the same persistence
shape covers Claude stream-json, ACP, and whatever comes next.

If a tab is closed and removed from the workspace, its JSONL is left
on disk (cheap, useful for future `/continue from <transcript>`). A
later janitor can prune.

## Per-driver capability

Each driver declares:

```python
class Driver(Protocol):
    supports_resume: bool

    def resume(self, session_id: str) -> AgentSession: ...
```

v1 reality check:

- **`ClaudeCode` (stream-json)** — `claude --resume <session-id>`.
  `supports_resume = True`. Session id latched from the first
  `system` event Claude emits on a fresh session.
- **`GeminiCLI` (ACP)** — ACP defines `session/load`, but
  gemini-cli's implementation has not been verified against this
  protocol path. Ship with `supports_resume = False`; flip in a
  follow-up once a `session/load` round-trip is exercised.
- **`OpenCode` (ACP)** — same posture as Gemini.

When a driver flips to `supports_resume = True`, its tabs become
resumable with no further changes to the workspace substrate.

### Session-id capture

The session id is produced by the underlying driver, not by aegis. It
is latched on the *first* provider event of a fresh session
(stream-json's `system` event for Claude; ACP's session establishment
for Gemini/OpenCode when they support it) and written into
`workspace.json` on the next tab-change write.

## Resume flow

On `aegis` startup with `workspace.json` present:

1. **Parse `workspace.json`.** If unparseable, print a clear error
   pointing at the file and exit nonzero. Suggest `aegis --clean`.
   Don't auto-recover by deleting.
2. **For each tab, classify**:
   - Profile no longer present in `.aegis.py` → **skip**.
   - Driver `supports_resume == False` → **skip**.
   - `session_id` missing or null → **skip**.
   - Otherwise → **resumable**.
3. **If zero resumable tabs**: print a single line listing what was
   skipped and exit clean. Don't open an empty TUI — surprising.

   ```
   $ aegis
   no resumable tabs (3 tabs in last workspace: 1 gemini, 2 opencode — driver does not support session resume)
   $
   ```
4. **Otherwise, open the TUI** with the resumable tabs in saved
   order, the saved active tab focused. For each tab, call
   `driver.resume(session_id)` and start a normal driver lifecycle.
5. **Redraw the transcript** for each tab from its JSONL — block by
   block, same renderers used at live time.
6. **Banner the skipped tabs** as a single line at the top of the
   active tab's pane:

   ```
   ↻ resumed 2 tabs · skipped 1 (profile "scratch" not in .aegis.py)
   ```

   No prompt. No modal. Alex can open them fresh manually if he wants.

### Mid-stream at shutdown

If aegis was killed while a turn was streaming, the last assistant
block in the JSONL is partial. On replay, drop the partial block and
mark the turn `⚠ interrupted` in the transcript. Don't auto-retry;
Alex re-sends if he wants the answer.

This is a transcript-level annotation only. The driver's resume call
still proceeds normally — Claude's server-side session record is its
own authority on what's in memory.

### Failure during resume

If `driver.resume(session_id)` raises (session expired, server
rejected, network error), surface that tab as **failed to resume**
with a one-line reason and continue with the rest. Failed tabs are
not auto-opened fresh — Alex opens them manually.

If *every* resumable tab fails, the TUI still opens (so Alex has a
place to land) with each pane showing the failure reason. Don't quit
on him.

## Edge cases

| Situation | Behavior |
|---|---|
| No `.aegis/state/workspace.json` | Fresh start, silent. |
| `workspace.json` corrupt | Exit nonzero with clear message; suggest `--clean`. Don't auto-recover. |
| Tab's profile removed from `.aegis.py` | Skip + report in startup line. |
| Driver no longer declares `supports_resume` | Skip + report in startup line. |
| Worker tab in workspace.json | Shouldn't happen (workers never written there), but if encountered: skip + ignore. |
| `aegis --clean` with workspace.json present | Read nothing; leave file untouched. First tab change overwrites. |
| Project moved on disk (cwd changed) | Claude's per-cwd session store can't find the session; resume fails per tab. Document the constraint; don't try to migrate sessions. |

## Out of scope

- **`/continue from <transcript>`** — cold-prime a fresh session with
  a prior transcript via handoff-style injection. Separate verb,
  separate spec.
- **`/resume` inside a tab** — pick from a library of prior
  conversations. Different mental model than workspace resume.
- **Named / multi workspaces** — `aegis --resume <name>`,
  `aegis save-workspace <name>`. YAGNI for v1.
- **`--clean=hard`** — wipe state directories. Manual `rm` is fine
  until proven otherwise.
- **Janitor / pruning of orphaned `sessions/*.jsonl`** — left on
  disk; future cleanup can be a separate maintenance command.

## Testing

Unit tests cover:

- `workspace.json` serialization / parse round-trip; corrupt-file
  rejection.
- Resume classification logic: each of the six skip reasons produces
  the right outcome on a synthetic workspace.
- Driver capability flag: a `FakeDriver` with `supports_resume = False`
  is consistently skipped; a `FakeDriver` with a passing resume opens
  the tab.
- Replay of `sessions/<handle>.jsonl` produces the same in-memory
  block list as the live render.
- Mid-stream truncated JSONL produces a turn marked `⚠ interrupted`.

Integration test: a real `ClaudeCode` driver opens a session, exchanges
two turns, aegis quits, `aegis` reopens, the resumed session answers a
follow-up that references the first turn — the model remembers.

## Implementation order

Five vertical slices, each a working slice through the substrate.

1. **Persistence substrate.** Write `workspace.json` and
   `sessions/<handle>.jsonl` live during a normal session. No resume
   yet — just produce the files and verify their shape.
2. **Driver capability.** Add `supports_resume` + `resume()` to the
   `Driver` protocol. Implement for `ClaudeCode` (latches session_id
   from first system event; calls `claude --resume <id>` on
   `resume`). Stub `supports_resume = False` on Gemini / OpenCode.
3. **CLI shape.** `aegis` reads `workspace.json` if present; `aegis
   --clean` ignores it. No-workspace and corrupt-workspace messaging.
4. **Resume flow.** Classify tabs, skip the unresumable, open the
   rest, redraw transcripts from JSONL, banner in the active pane.
5. **Edge handling.** Mid-stream interruption marker; per-tab resume
   failure shown in-pane; zero-resumable graceful exit.
