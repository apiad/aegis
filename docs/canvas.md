# Shared Canvas

A **canvas** is a markdown file multiple agents can read, write, and
subscribe to. When one agent writes, every other subscriber wakes up
with a notification carrying the diff — same `✉` inbox channel queues
and handoffs already use.

This is the [blackboard
pattern](https://en.wikipedia.org/wiki/Blackboard_(design_pattern)):
shared structure + specialists + event-driven coordination. Aegis has
three coordination primitives now:

| Primitive | Verb | Wake trigger |
|---|---|---|
| Queue | "do this, tell me when done" | Worker completes |
| Inbox / handoff | "wake — message for you" | Sender posts |
| **Canvas** | "wake — shared state changed" | Subscriber writes |

## The model

- A canvas is a real markdown file on disk — Alex can grep it, commit
  it, open it in his editor.
- Sections are `## headings`. Each section is the unit of write and
  the unit of notification.
- Any subscriber can write any section. The ledger records who wrote
  what; the inbox notification names the writer. No enforcement of
  ownership in v1 — trust the agents, surface conflicts loudly.

Special section names:

- `_preamble` — the body before the first `##` heading.
- `body` — the entire file, only valid when the file has no `##`
  headings.

## MCP tools

| Tool | Args | Returns |
|---|---|---|
| `aegis_canvas_open` | `name`, `file` (only on first open), `from_handle` | `{name, file, sections, created_at}` |
| `aegis_canvas_read` | `name`, `section` (optional), `from_handle` | `{content}` |
| `aegis_canvas_write_section` | `name`, `section`, `content`, `from_handle` | `{ok, canvas, section, op, writer, added, removed, timestamp}` |
| `aegis_canvas_append_to_section` | `name`, `section`, `text`, `from_handle` | same as above with `op=append` |
| `aegis_canvas_subscribe` | `name`, `from_handle`, `sections` (optional filter) | `{ok, subscribers}` |
| `aegis_canvas_unsubscribe` | `name`, `from_handle` | `{ok}` |
| `aegis_canvas_list` | — | list of canvas metadata |

`from_handle` is the calling agent's aegis handle (read from the
system prompt). It's used as the **writer** in the ledger and to
suppress the writer's own inbox echo.

## Notifications

When agent **alice** writes a section, every subscriber except alice
gets an inbox message like:

```
✉ from canvas:report-q3 · 2026-05-21T20:30:00Z
  section "data" · 2026-05-21T20:30:00Z
  written by agent:alice (+18 / -3 lines)
  ──
  ## Data
  Q3 numbers came in stronger than projected. Revenue up 14% YoY
  driven by enterprise tier expansion. Net new logos hit 47 …
  … (5 more lines)
```

`append_to_section` shows only the appended text (not the whole new
body). Subscription filters (`sections=["data"]`) only fire for
matching sections.

If aegis is restarted, the file + ledger persist but subscribers
don't — agents must re-subscribe.

## Worked example

```python
# PM agent
aegis_canvas_open(name="report-q3", file="vault/reports/q3.md",
                  from_handle="pm")
aegis_canvas_subscribe(name="report-q3", from_handle="pm")
aegis_canvas_write_section(
    name="report-q3", section="intro",
    content="Q3 was a quarter of consolidation…",
    from_handle="pm")
aegis_handoff(target_handle="researcher",
              context="fill the data section of canvas report-q3",
              from_handle="pm")

# researcher agent (woken by handoff)
aegis_canvas_open(name="report-q3", from_handle="researcher")
aegis_canvas_subscribe(name="report-q3", from_handle="researcher",
                       sections=["data"])
aegis_canvas_write_section(
    name="report-q3", section="data",
    content="Q3 numbers came in stronger…",
    from_handle="researcher")
# Done; returns.

# PM wakes with:
> from canvas:report-q3 · 2026-05-21T20:30:00Z
  section "data" · 2026-05-21T20:30:00Z
  written by agent:researcher (+1 / -0 lines)
  ──
  Q3 numbers came in stronger…
```

## State on disk

```
.aegis/state/canvases/<name>/
  meta.json              # {name, file, created_at}
  ledger.jsonl           # one append per write
```

The canvas content lives at whatever `file` path was passed on first
open — usually a project file or a vault note. The state dir is
gitignored by aegis defaults.

## Limitations (v1)

- **No external-edit detection.** If Alex edits the file in his
  editor between agent writes, agents don't get notified. The next
  write reads the current file state, so Alex's edits aren't lost,
  but they don't fan out as events. (A file watcher is on the
  follow-up list.)
- **No ownership enforcement.** Any subscriber can write any
  section.
- **Markdown only.** No HTML / JSON / structured canvases yet.
- **No subscription persistence across restarts.** Re-subscribe on
  each session.
- **No TUI surface.** Canvas content is the on-disk file; open it in
  your editor. Notifications appear in the agent's transcript as
  inbox blocks.

## Full spec

See `docs/superpowers/specs/2026-05-21-shared-canvas-design.md`.
