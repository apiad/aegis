---
title: Aegis Telegram — Renderer + Correctness (v0.11)
date: 2026-05-26
status: draft
---

# Aegis Telegram — Renderer + Correctness (v0.11)

## Motivation

v0.10 shipped the substrate **command surface** — 14 chat commands
backed by a registry pattern in `src/aegis/telegram/commands.py`.
What it did not touch is the path agent replies take to the user.
A 22-finding critique of the existing Telegram frontend named four
buckets of remaining work: **B** (renderer overhaul), **C** (voice
/ file I/O), **D** (correctness fixes), **E** (substrate-level
push). v0.10's spec listed all four as explicit non-goals.

This round ships **B + D together** — they touch the same two
files (`src/aegis/telegram/format.py`,
`src/aegis/telegram/frontend.py`), share testing infrastructure,
and the most visible bugs each addresses overlap (the silent
status-message freeze on long turns is both a B/UX symptom and a
D/rate-limit defect). Bucket C bleeds in only as a single Bot API
primitive — `sendDocument` — needed to land bucket B's #8 (silent
chunker truncation) cleanly. The rest of C (voice transcription,
inbound photo/document handling, outbound `sendPhoto`/`sendVoice`)
stays a non-goal.

Concrete failures this round eliminates:

- A worker reply containing a fenced code block (`/queue show` in
  v0.10 wraps tables in fences) renders today as literal triple
  backticks, because the agent-reply chunker calls
  `escape_md(text)` on the entire body and sends with
  `parse_mode=MarkdownV2` — every backtick becomes `\` plus a
  literal backtick. Same for bold, italic, links, lists.
- A worker reply >~20KB (full directory listing, long `git log`,
  large file dumped into the reply) drops to
  `"… (truncated, N more chunks)"` with no recovery path.
- A long turn (5+ tool calls, ≥2 min) freezes the status message
  silently — the 2-second refresh blows through Telegram's
  ~30 edits/min cap, the next edit returns `429`, and the loop
  swallows it.
- Restarting `aegis serve` mid-conversation replays every Telegram
  command from the last 24 hours.
- TUI + Telegram observing the same session: one clobbers the
  other's observer, because `core.on_event = ...` is a raw
  attribute assignment, not a list append.
- Tool calls during a turn are invisible from Telegram — the TUI
  shows `⏺ Read(file.py)` per call; Telegram sees only the final
  reply.
- Inbox envelopes (`> from queue:magpie:results · task#... · ok`)
  prepend invisibly to the user turn — they read as random
  greater-than noise above the actual content.

## Non-goals (explicit)

- **No voice / file inbound handling.** `frontend.handle_update`
  continues reading only `msg.get("text")`. Voice notes,
  documents, photos, captions get silently discarded. Bucket C
  remainder.
- **No outbound `sendPhoto`, `sendVoice`, media groups.** Only
  `sendDocument` (single file, no group, no caption media).
  Bucket C remainder.
- **No substrate-level `notify()` API.** Ad-hoc
  `bin/notify-telegram.sh` per job stays. Bucket E.
- **No Telegram-driven session-lifecycle audit JSONL.** Bucket E.
- **No `/format` command** to toggle HTML vs plain text. The
  renderer is always-on HTML; no escape hatch. If it breaks for
  some output, fix the renderer.
- **No spillover garbage collection.** Files at
  `<state_dir>/overflow/aegis-reply-*.md` accumulate forever. A
  separate sweep job can be added later if disk pressure shows up.
- **No `commands.py` refactor.** v0.10's registry just shipped;
  bucket B/D changes touch only `frontend.py` + `format.py` +
  `bot.py`. Command handlers already wrap tables in fences, which
  render correctly under HTML mode.

## Architecture overview

Three modules edited, one new module, one new third-party
dependency.

```
   ┌──────────────────────────────────────────────────────────────┐
   │  src/aegis/telegram/                                          │
   │    bot.py            ─── ADDS send_document (multipart POST)  │
   │                          ADDS parse_mode param to             │
   │                          send_message + edit_message          │
   │                                                               │
   │    format.py         ─── REPLACES. Becomes the                │
   │                          markdown→Telegram-HTML pipeline.     │
   │                          chunk() returns either list[str] or  │
   │                          Spillover (NamedTuple).              │
   │                                                               │
   │    format_html.py    ─── NEW. The markdown-it tokens →        │
   │                          Telegram-HTML walker.                │
   │                                                               │
   │    frontend.py       ─── EDITS. Status message becomes a      │
   │                          live per-turn ticker (envelope +     │
   │                          tool-call counts). Observer list,    │
   │                          offset persistence, _active cleanup, │
   │                          event-driven edits, None guard.      │
   │                                                               │
   │    commands.py       ─── UNCHANGED.                           │
   │                                                               │
   │  src/aegis/core/                                              │
   │    (manager.py / core.py)                                     │
   │                      ─── EDITS. on_event + on_state become    │
   │                          lists; add register_observer /       │
   │                          unregister_observer / on_session_   │
   │                          close.                               │
   └──────────────────────────────────────────────────────────────┘
```

**Dependency add:** `markdown-it-py` — pure-Python CommonMark
parser, ~25KB on disk, no native extensions needed. Added to
`pyproject.toml` runtime deps.

## Render path

The pipeline from agent-reply markdown to user-visible Telegram
message:

```
agent_reply_md (str)
        │
        ▼
  ┌─────────────────┐
  │ format_html     │   markdown-it parse → token stream → walker
  │   .render(md)   │   emitting only Telegram-supported HTML tags
  └─────────────────┘
        │
        ▼
  ┌─────────────────┐
  │ chunk(html,     │   split on paragraph then line boundaries,
  │   max_parts=3)  │   never inside <pre>, never inside an HTML
  └─────────────────┘   tag, each part ≤4096 chars
        │
        ├── ≤3 parts ──► bot.send_message(parse_mode="HTML") × N
        │
        └── >3 parts ──► spillover:
                          write raw md to <state_dir>/overflow/
                            aegis-reply-<ts>-<peer>.md,
                          render first 500 chars of md to HTML
                            as caption + footer,
                          bot.send_document(path, caption, "HTML")
```

### Supported HTML tag set

Telegram's Bot API allows only the following tags under
`parse_mode=HTML` (§formatting-options). The renderer emits
exactly these and nothing else:

```
<b>, <strong>       bold
<i>, <em>           italic
<code>              inline code
<pre>               preformatted block (with or without inner <code>)
<pre><code class="language-X">  fenced code, language label optional
<a href="…">        link
<blockquote>        block quote
<s>, <strike>       strikethrough
<u>                 underline
<tg-spoiler>        (not used in v0.11; reserved)
```

Markdown elements that do not have a Telegram-HTML counterpart
flatten with light visual cues:

- Headers (`#`, `##`, `###`) → `<b>…</b>\n` (bold line; level
  ignored because Telegram has no header tag).
- Unordered list items (`-`, `*`) → `• item\n`.
- Ordered list items → `1. item\n`, `2. item\n`, …
- Tables → fenced code block (`<pre>` of the markdown source) —
  monospace preserves column alignment better than any
  reflow-tags approximation.
- Horizontal rules → `\n───────\n`.
- Images → `[image: alt]` (placeholder; outbound images are
  bucket C).

### Escape rules

Escaping happens in exactly two places:

- **Text nodes:** `&` → `&amp;`, `<` → `&lt;`, `>` → `&gt;`.
  Nothing else.
- **Attribute values** (only `href`): the three above plus
  `"` → `&quot;`.

No other characters are escaped. MarkdownV2's 18-reserved-char
table goes away entirely.

### Chunker (`format.chunk`)

```python
class Spillover(NamedTuple):
    raw_md: str            # original agent markdown
    rendered_html: str     # full HTML (for the peek caption)

def chunk(html: str, raw_md: str, *,
          max_parts: int = 3,
          limit: int = 4096) -> list[str] | Spillover: ...
```

Splitting rules:

- Splits prefer paragraph boundaries (`\n\n`), fall back to line
  boundaries (`\n`).
- Never splits inside a `<pre>…</pre>` block or inside an HTML
  tag (the walker has full token positions; the chunker
  consumes HTML tokens, not raw chars).
- A single `<pre>` block alone exceeding 4096 chars forces
  spillover regardless of part count — there's no way to send
  it as a single message.
- If the natural split produces ≤3 parts that all fit ≤4096
  chars, returns `list[str]` of those parts.
- Otherwise (would need ≥4 parts, or a single part exceeds the
  hard limit), returns `Spillover(raw_md, rendered_html)`.

Part labels (`<i>handle (1/3)</i>\n…`) prefix every part when
N > 1; single-part replies have no label.

### Spillover file

- **Path:** `<state_dir>/overflow/aegis-reply-<ts>-<peer>.md`
  where `<ts>` is `YYYY-MM-DD-HHMMSS` and `<peer>` is the
  agent's handle.
- **Contents:** the raw agent markdown (not the HTML render).
  Reads cleanly in any markdown viewer including Obsidian's
  mobile app.
- **Caption template:**
  ```
  <i>handle</i>

  <peek>          ← first 500 chars of raw_md, rendered to HTML
  …

  📎 Full response (N chars) attached.
  ```
  (Telegram caps document captions at 1024 chars; 500 char peek
  + boilerplate fits comfortably.)

## Per-turn ticker (status message)

The status message — previously a `<handle> · working · model
metrics` line with a 2-second refresh — gets repurposed.

**One message per turn**, edited on event boundaries only:

```
turn start         →   ✉️ from queue:magpie:results · ⏳ thinking…
first tool call    →   ✉️ from queue:magpie:results · 🔧 Read x1
more tool calls    →   ✉️ from queue:magpie:results · 🔧 Read x3, Bash x1
turn end (ok)      →   ✉️ from queue:magpie:results · ✅ Read x3, Bash x1
turn end (error)   →   ✉️ from queue:magpie:results · ⚠️ <error class>
```

Rules:

- If the user turn has no envelope, drop the `✉️ … · ` prefix.
  Status becomes `⏳ thinking… / 🔧 … / ✅ …`.
- Edits triggered by core events: `tool_use_start`,
  `tool_use_end`, `turn_end`. Never on a timer.
- Telegram's ~30 edits/min cap is irrelevant unless an agent
  fires ≥30 tool calls in a minute. If it does, the renderer
  detects the boundary and switches to "edit on next quiet
  second" mode (collect tool counts, edit at most once per
  second). Worst case: ticker lags by 1s on bursts.
- On rate-limit `429`: log, await `retry_after`, retry once. If
  it fails twice, log and skip — the ticker becomes
  best-effort, the reply still lands.
- Status message **persists in chat after the turn** — becomes
  the permanent "here's what I did" artifact next to the reply.

This subsumes finding **#11** (tool calls invisible — they now
show up live on the ticker, and stay there) and **#10** (inbox
envelopes — ticker reflects them) and **D-#4** (status edit
rate-limit — gone, no more 2s timer).

## Envelope detection

`session.py` already exposes `on_inbox` — a primary callback that
fires synchronously at the top of `deliver()` for every incoming
`InboxMessage` (line 90-91). Telegram registers a handler that
records the latest envelope on the ticker's per-turn state:

```python
def _on_inbox(self, core, msg: InboxMessage) -> None:
    state = self._states.setdefault(core.handle, {})
    state["envelope"] = f"from {msg.sender.kind}:{msg.sender.handle}:{msg.sender.queue}"
```

The ticker then renders `✉️ {envelope} · …` on its next edit.

No envelope handling on the reply body itself — markdown
blockquotes (whether the worker echoes the envelope or anything
else) get the standard `> ` → `<blockquote>` mapping through the
renderer. Provenance shows up live on the ticker; if the worker's
reply also contains a blockquote, it renders as a blockquote.

## Observer migration

`session.py` already has the multi-observer machinery needed —
`add_event_observer` / `add_state_observer` (lines 65-71), each
maintaining an `_extra_*_observers: list` that fires *after* the
primary `on_event` / `on_state` slot. The substrate already uses
it (`aegis/queue/manager.py:270-271`). Only two consumers still
write to the primary slot directly:

- `src/aegis/telegram/frontend.py:55-56` (`core.on_event = on_event`, `core.on_state = on_state`)
- `src/aegis/tui/pane.py:264-265` (same pattern)

The fix is migration: both move to `add_event_observer(fn)` /
`add_state_observer(fn)`. The primary slot becomes unused for
these two consumers; the substrate's `QueueManager` continues
using the extras list. With both migrated, two frontends can
observe the same session without clobbering each other.

The primary `on_event`/`on_state` slots stay in `session.py` —
removing them is a bigger refactor and not blocking this round.
Reserve their removal for a follow-up.

### Close observer (new)

For D-#3 (`_active` cleanup on substrate-driven session close),
add a third observer pattern matching the existing two:

```python
# aegis/core/session.py
self.on_close: CloseCb | None = None
self._extra_close_observers: list[CloseCb] = []

def add_close_observer(self, cb: CloseCb) -> None: ...
def _emit_close(self, reason: str) -> None:
    if self.on_close is not None: self.on_close(self, reason)
    for cb in self._extra_close_observers:
        try: cb(self, reason)
        except Exception: log.exception(...)
```

`_emit_close` fires from every code path that tears down a
session: `/close` command, crash recovery, peer handoff
completion, substrate teardown. Reason is a string:
`"explicit" | "crash" | "handoff" | "teardown"`.

Telegram registers `add_close_observer(self._on_session_closed)`;
TUI does the same for its own per-pane cleanup. Try-wrap pattern
matches existing observers — one frontend raising on close
doesn't break the others.

## D fixes

### D-#2 — Offset persistence

```python
# aegis/telegram/frontend.py
async def run(self, bot) -> None:
    offset = self._load_offset()
    while True:
        for up in await bot.get_updates(offset):
            offset = up["update_id"] + 1
            self._save_offset(offset)            # atomic tmp+rename
            try: await self.handle_update(up)
            except Exception: log.exception(...)

def _offset_path(self) -> Path:
    return self._state_dir / "telegram.offset"

def _load_offset(self) -> int:
    try:
        return int(self._offset_path().read_text().strip())
    except FileNotFoundError:
        return 0
    except ValueError:
        log.warning("telegram.offset corrupt; starting at 0")
        return 0

def _save_offset(self, offset: int) -> None:
    p = self._offset_path()
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(f"{offset}\n")
    tmp.replace(p)
```

Missing file → start at 0 (first run). Corrupt contents → start
at 0 with a warning log. The frontend gains a `state_dir`
constructor parameter, threaded through from `cli.py`
(typically `<workspace>/.aegis/state/`).

### D-#3 — `_active` cleanup

`frontend.py` registers a close observer at start that clears
`_active` when the named session closes:

```python
def _on_session_closed(self, core, reason):
    if self._active == core.handle:
        self._active = None
        # don't message the chat about it; the next user input gets
        # the "no active agent" reply naturally
```

The existing line at `frontend.py:117-119` (clearing `_active`
on lookup miss) stays as defense in depth.

### D-#5 — `send_message=None` guard

Wrap the `state["mid"]` assignment in a try/except:

```python
try:
    mid = await self._bot.send_message(
        self._chat, line, parse_mode="HTML")
except Exception:
    log.exception("status message send failed; turn proceeds without ticker")
    return                # skip refresh setup
if mid is None:
    log.warning("send_message returned None; no ticker for this turn")
    return
state["mid"] = mid
```

If the status send fails, the turn proceeds without a ticker;
the agent reply still lands when complete.

### D-#6 — refresh loop catches all exceptions

The `_refresh_loop` goes away entirely (replaced by
event-driven edits). The new event handler that does the edit
catches `Exception`, logs, and continues:

```python
async def _edit_status(self, state, html: str) -> None:
    mid = state.get("mid")
    if mid is None: return
    try:
        await self._bot.edit_message(
            self._chat, mid, html, parse_mode="HTML")
    except Exception:
        log.exception("status edit failed; ticker may freeze for this turn")
```

A bad status edit no longer kills any background task.

## Bot API additions

```python
# aegis/telegram/bot.py
async def send_message(
    self, chat_id: int, text: str,
    *, parse_mode: str | None = None,
) -> int | None: ...

async def edit_message(
    self, chat_id: int, message_id: int, text: str,
    *, parse_mode: str | None = None,
) -> None: ...

async def send_document(
    self, chat_id: int, path: Path, *,
    caption: str | None = None,
    parse_mode: str | None = None,
) -> int | None:
    """multipart POST to sendDocument."""
```

The existing `markdown: bool` parameter on `send_message` is
removed (no backward compat shim — every caller in `aegis/`
either passes nothing or moves to `parse_mode="HTML"`).

`send_document` is a new HTTP shape — multipart `POST`, not the
existing `GET` with query params used by other Bot API methods.
Implementation uses `httpx.AsyncClient.post(...,
files={"document": (filename, fp)}, data={...})`.

## Testing

### Unit (`tests/telegram/`)

| Module | Tests |
|---|---|
| `format_html.py` | Round-trip corpus of agent-reply markdown → HTML. Assert: no unescaped `<>&` in text nodes; only supported tags emitted; fenced code blocks preserve language label; nested formatting (bold inside list inside blockquote) preserves structure; href values with `&`, `<`, `"` are correctly escaped; inline code containing `<script>` renders as `&lt;script&gt;`. |
| `format.chunk()` | (a) Single paragraph → returns `[html]`. (b) Body that greedy-packs into ≤3 parts (each ≤4096 chars) → returns those parts; pack is greedy, not 1:1 paragraph:part. (c) Body that needs ≥4 parts after greedy packing → returns `Spillover`. (d) Single `<pre>` >4096 chars → returns `Spillover`. (e) Splits never land inside `<pre>` or inside an HTML tag. |
| `bot.send_document` | Mocks Telegram's multipart endpoint via `httpx.MockTransport`; asserts correct `Content-Type`, file upload field name `document`, caption + parse_mode form fields. |
| `bot.send_message / edit_message` | Asserts `parse_mode=HTML` flows through to the URL params. |
| `core observers` | (a) Two observers both fire. (b) One raising doesn't suppress the other. (c) Unregister actually removes. (d) `SessionClosed` fires on every close path. |
| `offset persistence` | (a) Save N → reload returns N. (b) Missing file → returns 0. (c) Corrupt file → returns 0 with warning. (d) Atomic save: tmp file always cleaned. |

### Integration (`tests/telegram/test_frontend_e2e.py`)

A `MockBot` recording every API call. One full turn through the
core with a scripted event sequence; assert exact sequence of
bot calls:

```
1.  send_message(chat, "✉️ … · ⏳ thinking…", parse_mode="HTML")
2.  edit_message(chat, mid, "✉️ … · 🔧 Read x1", parse_mode="HTML")
3.  edit_message(chat, mid, "✉️ … · 🔧 Read x2, Bash x1", parse_mode="HTML")
4.  edit_message(chat, mid, "✉️ … · ✅ Read x2, Bash x1", parse_mode="HTML")
5.  send_message(chat, "<reply HTML>", parse_mode="HTML")
```

Plus a `test_overflow_e2e` variant where the reply needs >3
parts: asserts `send_document(chat, <path>, caption="<peek>…", parse_mode="HTML")` fires instead of multi-part
`send_message`, and verifies the file on disk matches the raw
agent markdown.

Plus a `test_two_frontends_e2e` variant: register TUI + Telegram
observers on the same core; assert both see every event.

### Manual verification

After deploying v0.11 to zion (and later to VPS via the bumped
aegis):

1. Send a worker reply with intentional markdown (fenced code,
   bold, link) — all three render natively. No literal
   backslashes.
2. Trigger a long turn (5+ tool calls, ≥2 min) — status message
   updates live; ticker shows growing tool counts; no freeze.
3. Trigger a reply ~30KB — `.md` attachment lands with peek
   caption.
4. Restart `aegis serve` mid-conversation — no replay of
   pre-restart Telegram updates after restart.
5. Run TUI + Telegram side-by-side against the same session —
   both see all events.
6. Trigger a handoff (`magpie` → `warden`) — status ticker
   shows `✉️ from queue:magpie:results · …`; reply body
   contains a `<blockquote>` if the worker echoes the
   envelope.

## Release

PyPI bump to `v0.11.0`. Tag `v0.11.0`, push, release workflow
runs against `release.yml` (unchanged from v0.10). VPS pulls
the bumped wheel; sync-guard validates; jobs continue using
the new renderer transparently.
