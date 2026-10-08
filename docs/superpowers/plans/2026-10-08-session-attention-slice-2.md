# Session attention, slice 2: reading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** aegis knows which agent messages a person has read, on any browser: unread messages carry a margin mark, done and review badges clear once read, a divider marks where you stopped reading, a navigator walks agent messages, and the page title, favicon and desktop notifications ping when a session needs you.

**Architecture:** The session keeps the set of unread agent-message ids (prose entries) and the time of the last read, both in its meta, so boot still reads no store. New prose entries join the set as the fold publishes them; a person's `session.read` removes ids. The transcript channel publishes a *view* of the entries in which each prose entry carries `unread`; the fold's own entries stay untouched, so a refold still equals the live entries. `attention.card` takes the unread count and decides the `mark` a tab draws (a review or done badge becomes the plain idle dot once read) and whether a needs-you badge blinks. The client watches prose rows on screen and reports reads in batches.

**Tech Stack:** Python 3.13, pydantic, pytest; plain ES modules, Playwright browser tests.

**Spec:** `docs/superpowers/specs/2026-10-08-session-attention-design.md`, slice 2 ("Reading"): sections "When a mark shows", "What you have read", "The ping", "Glyphs".

## Global Constraints

- Boot reads meta files only, never stores (`tests/test_registry.py::test_boot_reads_no_store_when_every_meta_is_there`).
- A fold of the store equals the session's entries (`refold_matches` in `tests/test_session.py`), and a browser's snapshot plus every patch equals what the channel serves (`patches_rebuild_entries`). The `unread` flag lives only in the channel's view, never in the fold.
- Python decides, the browser draws: unread flags, the count, `mark` and `blink` are computed in Python. The client computes only positions (where the divider goes, "message 3 of 4").
- A message counts as read when its row has been at least half visible for one second while the page is visible and focused.
- `session.read` is for people only (not an agent operation).
- A session whose meta predates this slice has no unread messages.
- Mark rules: working, error, waiting and needs_you always show their badge; review and done show theirs while the session has unread messages and the plain idle dot after; needs_you blinks only while it has unread messages and its tab is not focused.
- Glyphs are inline SVG in `currentColor`, added to the sprite in `js/glyphs.js`.
- Keys: Alt+↑ / Alt+↓ previous / next agent message, Alt+U first unread, one row each in `js/keys.js`.
- The page title starts with "(n) " where n counts sessions whose attention is needs_you or error, in every view; the favicon gets a dot in the accent colour while n > 0.
- A desktop notification fires only on a transition into needs_you or error, only while the page is hidden, only with permission granted from the bell button; tag `<log_id>:<attention>:<attention_line>`.
- Stage named paths only, conventional commits ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`, never amend. Work in `.claude/worktrees/session-attention-2`, branch `session-attention-slice-2`.

## Review Focus

- A person reading on the phone while the laptop shows the same tab: the laptop's marks must flip without a reload (published upserts), pinned in Task 1.
- An agent message that arrives while the person is watching the tab must be marked read after a second, not stay unread, pinned in Task 3.
- Reads for ids that are not unread (already read, unknown, from another session) are ignored without error, pinned in Task 2.
- The divider must not become a selectable row: j/k and the navigator must never select it, pinned in Task 4.
- Notifications must not fire for the states sessions are already in when the page loads, pinned in Task 5.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/aegis/session.py` (modify) | `unread` set and `last_read_at` in meta and card; `view()`; dressed patches; `read(ids)` |
| `src/aegis/registry.py` (modify) | builds both from the meta; the transcript resolver serves `view` |
| `src/aegis/attention.py` (modify) | `unread` input; `mark` and `blink` outputs |
| `src/aegis/app.py` (modify) | `session.read` operation; `mark` and `blink` in the urgent tuple |
| `src/aegis/client/js/glyphs.js` (modify) | unread, read, up, down, latest symbols; `icon(name)` |
| `src/aegis/client/js/entries.js` (modify) | the prose row's margin mark |
| `src/aegis/client/js/transcript.js` (modify) | the read watcher, the divider, the navigator's moves |
| `src/aegis/client/js/tabs.js`, `fleet.js` (modify) | draw `mark`, `blink` |
| `src/aegis/client/js/ping.js` (create) | title count, favicon, notifications |
| `src/aegis/client/js/app.js`, `keys.js`, `index.html`, `css/base.css` (modify) | wiring, keys, markup, styles |
| tests | `tests/test_session.py`, `tests/test_registry.py`, `tests/test_attention.py`, `tests/test_attention_e2e.py`, `tests/test_browser.py` |

---

### Task 1: The session's read state and the channel's view

**Files:**
- Modify: `src/aegis/session.py`, `src/aegis/registry.py`
- Test: `tests/test_session.py`, `tests/test_registry.py`

**Interfaces:**
- Produces: `Session.unread: set[str]`, `Session.last_read_at: float | None`, `Session.view() -> list[dict]` (the fold's entries, each prose entry copied with `"unread": bool`), `Session.read(ids: list[str]) -> int` (ids removed; publishes the changed entries on the transcript channel and the card at once; returns the number removed). Meta keys `"unread"` (a sorted list) and `"last_read_at"`. `wire()` carries `"unread"` as an int count and `"last_read_at"`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_session.py`)

```python
async def test_agent_messages_arrive_unread_and_a_read_clears_them(h):
    await h.session.send("hello")
    await until(lambda: h.session.status == "idle" and h.session.unread, what="a reply")
    (pid,) = [e["id"] for e in h.session.entries() if e["kind"] == "prose"]
    assert h.session.unread == {pid}
    assert [e["unread"] for e in h.session.view() if e["kind"] == "prose"] == [True]
    assert "unread" not in next(e for e in h.session.entries() if e["id"] == pid)
    assert h.session.wire()["unread"] == 1
    n = len(h.published)
    assert h.session.read([pid, "nope"]) == 1
    assert h.session.unread == set() and h.session.last_read_at
    ups = [op["upsert"] for ch, ops in h.published[n:] if ch == h.session.channel for op in ops]
    assert [(u["id"], u["unread"]) for u in ups] == [(pid, False)]
    cards = [op["upsert"] for ch, ops in h.published[n:] if ch == "sessions" for op in ops]
    assert cards and cards[-1]["unread"] == 0
    assert h.session.read([pid]) == 0  # already read: nothing published


async def test_read_state_survives_a_rebuild_and_an_old_meta_has_none(tmp_path, fake_claude):
    h = Harness(tmp_path, fake_claude)
    assert h.session.unread == set() and h.session.last_read_at is None
    await h.session.start()
    await h.session.send("hello")
    await until(lambda: h.session.status == "idle" and h.session.unread, what="a reply")
    unread = set(h.session.unread)
    await h.session.shutdown()
    meta = h.metas.read_all()[0][0]
    assert set(meta["unread"]) == unread
    again = h.make(unread=meta["unread"], last_read_at=meta["last_read_at"])
    assert again.unread == unread
```

In the `Harness` class, change `patches_rebuild_entries` to compare against what the channel serves:

```python
        return list(shown.values()) == self.session.view()
```

and every place in `tests/test_session.py` that sets `h.snapshot = ... .entries()` to use `.view()` (grep for `snapshot =`).

In `tests/test_registry.py`, add a boot test in the style of `test_boot_takes_the_standing_from_the_meta`: write a meta with `"unread": ["e5.0"]` and `"last_read_at": 1234.0`, boot, and assert the session's `unread == {"e5.0"}`, `wire()["unread"] == 1` and `wire()["last_read_at"] == 1234.0`.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -q tests/test_session.py tests/test_registry.py -k "unread or read_state or read or boot"`
Expected: FAIL with `AttributeError: 'Session' object has no attribute 'unread'` (and `view`).

- [ ] **Step 3: Implement** (`src/aegis/session.py`)

`Session.__init__` gains keywords `unread: list[str] | None = None` and `last_read_at: float | None = None`, and sets:

```python
        # Agent messages (prose entries) no person has read yet, on any browser,
        # and when someone last read. In the meta, so boot needs no store; a meta
        # from before this has neither, and nothing old turns up unread.
        self.unread: set[str] = set(unread or ())
        self.last_read_at = last_read_at
```

`meta()` adds `"unread": sorted(self.unread), "last_read_at": self.last_read_at,`. `wire()` replaces the list with its count, after the existing pops:

```python
        m["unread"] = len(self.unread)
```

Add `"unread"` to `_SOON` (a new message changes the count on nearly every turn; `read` publishes at once itself).

Add next to `entries()`:

```python
    def view(self) -> list[dict]:
        """The entries as the transcript channel serves them: each agent message
        carries whether it is unread. The fold's entries never carry it, so a
        refold of the store still equals them."""
        return [self._dress(e) for e in self.entries()]

    def _dress(self, e: dict) -> dict:
        return {**e, "unread": e["id"] in self.unread} if e["kind"] == "prose" else e

    def read(self, ids: list[str]) -> int:
        """A person read these agent messages; ids that are not unread are
        ignored. Publishes the changed entries and the card at once."""
        hit = [i for i in dict.fromkeys(ids) if i in self.unread]
        if not hit:
            return 0
        # _set writes the meta; the card goes out at once below, not in a batch.
        self._set(unread=self.unread - set(hit), last_read_at=time.time())
        by_id = {e["id"]: e for e in self.fold().entries()}
        self._publish(
            self.channel,
            [{"upsert": self._dress(by_id[i])} for i in hit if i in by_id],
        )
        self._publish_now()
        return len(hit)
```

In `_record`, every prose upsert the fold emits is a new agent message (prose entries are created once, from a `Text` event, and never updated): add it to the set and dress the ops before publishing. Replace `self._publish(self.channel, ops)` with:

```python
        new = [op["upsert"]["id"] for op in ops if op.get("upsert", {}).get("kind") == "prose"]
        if new:
            # Before dressing the ops, so the new rows go out unread. "unread" is
            # in _SOON: the card's count follows within PUBLISH_EVERY_S.
            self._set(unread=self.unread | set(new))
        self._publish(
            self.channel,
            [{"upsert": self._dress(op["upsert"])} if "upsert" in op else op for op in ops],
        )
```

`_set` compares by value and treats `"unread"` like any other field, so the card's count reaches the `sessions` channel within `PUBLISH_EVERY_S` of a reply; Task 2's e2e test asserts the published card.

In `src/aegis/registry.py`: the `Session(...)` built from a meta passes `unread=meta.get("unread"), last_read_at=meta.get("last_read_at"),`. In `transcript()`, a live session's snapshot function becomes `s.view` instead of `s.entries`. An archived session's snapshot stays the raw fold (an archived session has no live read state; its prose rows carry no flag and draw no mark).

- [ ] **Step 4: Run them to see them pass**

Run: `uv run pytest -q tests/test_session.py tests/test_registry.py`
Expected: all pass, including `refold_matches` and `patches_rebuild_entries` in the fixture teardown.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/session.py src/aegis/registry.py tests/test_session.py tests/test_registry.py
git commit -m "feat(session): which agent messages are unread, kept in the meta and served in the transcript's view (#171)"
```

---

### Task 2: Marks that clear on read, and the `session.read` operation

**Files:**
- Modify: `src/aegis/attention.py`, `src/aegis/registry.py` (`card`), `src/aegis/app.py`
- Test: `tests/test_attention.py`, `tests/test_attention_e2e.py`

**Interfaces:**
- Consumes: `Session.unread`, `Session.read` (Task 1).
- Produces: `attention.card(..., unread: int = 0)` adds keys `mark` (one of the six attention values or `"idle"`) and `blink: bool`; operation `session.read` with params `{log_id: str, ids: list[str]}` (at most 500 ids), person-only, returning `{"read": int, "unread": int}`.

- [ ] **Step 1: Write the failing tests**

`tests/test_attention.py`:

```python
@pytest.mark.parametrize(
    "standing, kw, unread, mark, blink",
    [
        (st(report=rep("done")), {}, 1, "done", False),
        (st(report=rep("done")), {}, 0, "idle", False),
        (st(report=rep("review")), {}, 0, "idle", False),
        (st(report=rep("review")), {}, 2, "review", False),
        (st(report=rep("needs_you")), {}, 1, "needs_you", True),
        (st(report=rep("needs_you")), {}, 0, "needs_you", False),
        (st(turn_error="boom"), {}, 0, "error", False),
        (st(), {"waits": ["1 monitor"]}, 0, "waiting", False),
        (st(), {"working": True}, 0, "working", False),
    ],
)
def test_review_and_done_clear_on_read_and_needs_you_blinks_while_unread(standing, kw, unread, mark, blink):
    c = card(standing, working=kw.get("working", False), worker=False, waits=kw.get("waits", []), unread=unread)
    assert (c["mark"], c["blink"]) == (mark, blink)
```

`tests/test_attention_e2e.py` (uses the `published` fixture and helpers already in that file):

```python
async def test_reading_the_reply_clears_a_done_mark_on_the_published_card(world, published):
    a = await world.spawn()
    await turn(a, mcp("turn_end", attention="done", line="did it", replies=[]))
    await until(lambda: published(a.log_id).get("mark") == "done", what="the done mark")
    ids = [e["id"] for e in a.view() if e["kind"] == "prose" and e["unread"]]
    assert ids
    r = await world.app.registry.call("session.read", {"log_id": a.log_id, "ids": ids + ["e0.0"]})
    assert r == {"read": len(ids), "unread": 0}
    await until(lambda: published(a.log_id).get("mark") == "idle", what="the cleared mark")
    assert published(a.log_id)["attention"] == "done"


async def test_session_read_is_for_people_only(world):
    from aegis.ops import Caller

    a = await world.spawn()
    with pytest.raises(OpError) as e:
        await world.app.registry.call("session.read", {"log_id": a.log_id, "ids": []}, Caller("agent", a.log_id))
    assert e.value.code == "not_for_agents"
```

Check how `published` is named and shaped in `tests/test_attention_e2e.py` before writing these (it records the `sessions` channel; adapt the helper call to its real signature).

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -q tests/test_attention.py tests/test_attention_e2e.py -k "read or clear or blink"`
Expected: FAIL (`unexpected keyword argument 'unread'`, `unknown_op`).

- [ ] **Step 3: Implement**

`src/aegis/attention.py`: add `unread: int = 0` to `card`'s keyword parameters and, before the `return`, compute:

```python
    # A review or done badge is for messages not yet read; once read, the tab
    # shows the plain idle dot. A needs_you badge stays until the person sends,
    # because the session is still blocked on them; it blinks only while unread.
    mark = "idle" if kind in ("review", "done") and unread == 0 else kind
    blink = kind == "needs_you" and unread > 0
```

and add `"mark": mark, "blink": blink,` to the returned dict. Extend the module docstring with one sentence on `mark`.

`src/aegis/registry.py` `card`: pass `unread=len(session.unread)`.

`src/aegis/app.py`:

```python
class ReadParams(_Strict):
    log_id: str
    ids: list[str] = Field(max_length=500)
```

registered next to `session.send`, without `agent=True`:

```python
        @r.op("session.read", ReadParams)
        async def read(p: ReadParams, caller):
            """A person read these agent messages, on any browser."""
            s = reg.open(p.log_id)
            n = s.read(p.ids)
            return {"read": n, "unread": len(s.unread)}
```

(Check `reg.open` is the helper `session.send` uses for an open session; use the same one.)

In `App._sessions_key`, add `m.get("mark"), m.get("blink")` to the `seen` tuple, and name them in its docstring: a mark clearing or starting to blink is a change a person acts on.

- [ ] **Step 4: Run them to see them pass**

Run: `uv run pytest -q tests/test_attention.py tests/test_attention_e2e.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/attention.py src/aegis/registry.py src/aegis/app.py tests/test_attention.py tests/test_attention_e2e.py
git commit -m "feat(attention): done and review marks clear once read; session.read for people (#171)"
```

---

### Task 3: Margin marks, the read watcher, and marks on tabs and cards

**Files:**
- Modify: `src/aegis/client/js/glyphs.js`, `src/aegis/client/js/entries.js`, `src/aegis/client/js/transcript.js`, `src/aegis/client/js/tabs.js`, `src/aegis/client/js/fleet.js`, `src/aegis/client/js/app.js`, `src/aegis/client/css/base.css`
- Test: `tests/test_browser.py`

**Interfaces:**
- Consumes: prose entries' `unread`; card `mark`, `blink`, `unread` (Tasks 1-2); operation `session.read`.
- Produces: `glyphs.js` `icon(name) -> SVGElement` for `unread`, `read`, `up`, `down`, `latest`; `new Transcript(scroller, list, jump, { onRead })`, where `onRead(ids)` is called with batches of unread prose ids the reader has seen.

- [ ] **Step 1: Write the failing browser test** (append to `tests/test_browser.py`)

```python
def test_a_reply_read_on_screen_turns_its_mark_and_clears_the_done_badge(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "hello")
    report(page, attention="done", line="did it", replies=[])
    turns_done(page, 2)
    # On screen and focused for a second: both replies become read.
    page.wait_for_function(
        "() => document.querySelectorAll('.row.prose .rm .ic.read').length >= 2"
        " && !document.querySelector('.row.prose .rm .ic.unread')",
        timeout=6000,
    )
    page.wait_for_selector(".tab.on .dot.ready")  # done, read: the plain idle dot
    assert page.errors == []


def test_a_reply_that_lands_while_you_are_away_stays_unread_until_you_look(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "hello")
    sid = page.evaluate("location.hash.slice(3)")
    # The first reply is on screen: it becomes read before we leave.
    page.wait_for_function("() => !document.querySelector('.row.prose .rm .ic.unread')", timeout=6000)
    page.fill("#input", "/sleep 1")
    page.press("#input", "Enter")
    page.click("#tab-fleet")  # away while the reply arrives
    page.wait_for_selector(f".card[data-id='{sid}'] .ft >> text=1 unread", timeout=8000)
    page.click(f".tab[data-id='{sid}']")
    page.wait_for_selector(".row.prose .rm .ic.unread", state="attached")
    page.wait_for_function("() => !document.querySelector('.row.prose .rm .ic.unread')", timeout=6000)
    assert page.errors == []
```

(The Fleet card's footer shows "N unread" when `m.unread > 0`; that is part of this task.)

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -q -m browser tests/test_browser.py -k "read_on_screen or while_you_are_away"`
Expected: FAIL waiting for `.rm .ic.read`.

- [ ] **Step 3: Glyphs** (`glyphs.js`)

Add to `SPRITE`'s `<defs>`:

```javascript
<symbol id="g-unread" viewBox="0 0 16 16"><circle cx="8" cy="8" r="3.2" fill="currentColor"/></symbol>
<symbol id="g-read" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3.6 8.4l2.9 2.9 5.9-6.4"/></symbol>
<symbol id="g-up" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 10l4-4 4 4"/></symbol>
<symbol id="g-down" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6l4 4 4-4"/></symbol>
<symbol id="g-latest" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M8 2.5v8M4.6 7.3L8 10.7l3.4-3.4M3.8 13.5h8.4"/></symbol>
```

and export:

```javascript
// A plain symbol by name: the read marks and the navigator's arrows.
export function icon(name) {
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("class", `ic ${name}`);
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(NS, "use");
  use.setAttribute("href", `#g-${name}`);
  svg.append(use);
  return svg;
}
```

- [ ] **Step 4: The prose margin mark** (`entries.js`)

```javascript
import { icon } from "./glyphs.js";
```

In `RENDERERS.prose`, after building the row:

```javascript
  prose(e) {
    const body = markdown(e.md);
    body.classList.add("body");
    const r = row(e, "prose", body);
    // Only the live view carries the flag; an archived transcript draws no mark.
    if (e.unread !== undefined) {
      const rm = el("span", "rm");
      rm.append(icon(e.unread ? "unread" : "read"));
      r.append(rm);
      r.classList.toggle("unread", e.unread);
    }
    return r;
  },
```

- [ ] **Step 5: The read watcher** (`transcript.js`)

The constructor takes a fourth argument `{ onRead = () => {} } = {}`. Add, in the constructor:

```javascript
    // Reading: an unread agent message counts as read once its row has been at
    // least half visible for a second while the page is visible and focused.
    this.onRead = onRead;
    this.since = new Map(); // id -> when it became half visible
    this.sent = new Set(); // ids reported and not yet echoed back as read
    this.watch = new IntersectionObserver(
      (items) => {
        for (const it of items) {
          const id = it.target.dataset.id;
          if (it.isIntersecting) this.since.set(id, performance.now());
          else this.since.delete(id);
        }
      },
      { root: scroller, threshold: 0.5 },
    );
    const looking = () => document.visibilityState === "visible" && document.hasFocus();
    window.addEventListener("focus", () => {
      for (const id of this.since.keys()) this.since.set(id, performance.now());
    });
    setInterval(() => {
      if (!looking()) return;
      const now = performance.now();
      const ids = [];
      for (const [id, t] of this.since) {
        if (now - t >= 1000 && this.entries.get(id)?.unread && !this.sent.has(id)) ids.push(id);
      }
      if (ids.length) {
        for (const id of ids) this.sent.add(id);
        this.onRead(ids);
      }
    }, 300);
```

In `mount(e)`, after `this.nodes.set(e.id, n)`: `if (e.unread) this.watch.observe(n);` In `apply`, when an upsert replaces a node or a row is removed, call `this.watch.unobserve(old)` on the old node; when an upsert says `unread: false`, `this.sent.delete(e.id)`. In `clear()`: `this.watch.disconnect(); this.since.clear(); this.sent.clear();`. Where rows are removed from the top in `apply` (the `while (this.nodes.size > WINDOW + PAGE)` loop), also `this.watch.unobserve(first)` and `this.since.delete(first.dataset.id)`.

- [ ] **Step 6: Wiring, tabs, cards** (`app.js`, `tabs.js`, `fleet.js`)

`app.js`: construct the transcript with the reporter:

```javascript
const transcript = new Transcript($("tr"), $("entries"), $("jump"), {
  onRead: (ids) => {
    if (shown) conn.call("session.read", { log_id: shown, ids }).catch(() => {});
  },
});
```

(`shown` is the log id `follow()` subscribed to; a failed report is retried by the next tick because the ids stay unread in the view. Clear them from `transcript.sent` in the catch: `.catch(() => ids.forEach((i) => transcript.sent.delete(i)))`.)

`tabs.js`: draw the mark instead of the attention:

```javascript
  const mark = m.mark === "idle" ? Object.assign(document.createElement("span"), { className: "dot ready" }) : glyph(m.mark || m.attention);
  t.classList.toggle("blink", !!m.blink);
```

`fleet.js` `card()`: the header glyph uses the same rule (a small helper `markNode(m)` in `fleet.js` that returns the dot or `glyph(...)`, used by the card; `tabs.js` may import it from `fleet.js` or keep its own two lines, matching how the two files already share helpers). In the footer, after the cost, `if (m.unread) ft.append(el("span", "unr", `${m.unread} unread`));`.

- [ ] **Step 7: Styles** (`base.css`)

```css
/* reading: the margin mark on agent messages */
#a2 .row.prose{grid-template-columns:var(--row-cols) 16px}
#a2 .row.prose .rm{line-height:1.9;text-align:center}
#a2 .row.prose .rm .ic{width:12px;height:12px}
#a2 .row.prose .rm .ic.unread{color:var(--accent)}
#a2 .row.prose .rm .ic.read{color:var(--faint)}
#a2 .card .ft .unr{color:var(--accent)}
```

and replace the blink selector `#a2 .tab:not(.on) .ic.need{...}` with `#a2 .tab.blink:not(.on) .ic.need{...}` (keep its reduced-motion rule in step).

- [ ] **Step 8: Run the browser tests that touch these**

Run: `uv run pytest -q -m browser tests/test_browser.py -k "read_on_screen or while_you_are_away or question_marks or reply_pills or patch_redraws or long_transcript"`
Expected: PASS, none skipped.

- [ ] **Step 9: Commit**

```bash
git add src/aegis/client/js/glyphs.js src/aegis/client/js/entries.js src/aegis/client/js/transcript.js src/aegis/client/js/tabs.js src/aegis/client/js/fleet.js src/aegis/client/js/app.js src/aegis/client/css/base.css tests/test_browser.py
git commit -m "feat(client): unread marks on agent messages, reads reported from what is on screen, marks that clear (#171)"
```

---

### Task 4: The divider, the navigator and its keys

**Files:**
- Modify: `src/aegis/client/js/transcript.js`, `src/aegis/client/js/app.js`, `src/aegis/client/js/keys.js`, `src/aegis/client/index.html`, `src/aegis/client/css/base.css`
- Test: `tests/test_browser.py`

**Interfaces:**
- Consumes: prose entries' `unread`; card `last_read_at`; `icon()` (Task 3).
- Produces: `Transcript.setSince(text)`, `Transcript.message(delta)`, `Transcript.firstUnread()`, `Transcript.position() -> {index, total, unread}`; key actions `msgPrev`, `msgNext`, `firstUnread`.

- [ ] **Step 1: Write the failing browser test**

```python
def test_the_divider_and_navigator_walk_agent_messages(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "one")
    sid = page.evaluate("location.hash.slice(3)")
    page.wait_for_function("() => !document.querySelector('.row.prose .rm .ic.unread')", timeout=6000)
    # A reply lands while away: a one-second turn, and the Fleet before it ends.
    page.fill("#input", "/sleep 1")
    page.press("#input", "Enter")
    page.click("#tab-fleet")
    page.wait_for_selector(f".card[data-id='{sid}'] .ft >> text=1 unread", timeout=8000)
    page.click(f".tab[data-id='{sid}']")
    page.wait_for_selector(".row.since")
    since = page.eval_on_selector(".row.since", "n => getComputedStyle(n, '::before').content")
    assert "new since you left" in since
    # On screen, it is read within a second; the divider stays where it was.
    page.wait_for_function("() => document.querySelector('#nav-pos').textContent.startsWith('message ')", timeout=6000)
    assert page.locator(".row.since").count() == 1
    page.click(".nav .up")
    assert page.eval_on_selector(".row.sel", "n => n.classList.contains('prose')")
    page.keyboard.press("Alt+ArrowDown")
    assert page.eval_on_selector(".row.sel", "n => n.classList.contains('prose')")
    page.keyboard.press("j")  # the divider is a style, never a row of its own
    assert page.eval_on_selector(".row.sel", "n => !!n.dataset.id")
    assert page.errors == []
```

The fake claude's `/sleep 1` answers after a one-second Bash call, which leaves time to switch to the Fleet before the reply lands.

- [ ] **Step 2: Run it to see it fail**

Run: `uv run pytest -q -m browser tests/test_browser.py -k divider_and_navigator`
Expected: FAIL waiting for `.row.since`.

- [ ] **Step 3: The divider** (`transcript.js`)

```javascript
  // "New since you left": a style on the row it sits above, never a row of its
  // own, so j/k and the navigator cannot land on it. Placed once, when the tab
  // is opened, after the last read agent message before the first unread one,
  // and left there while the tab stays open.
  setSince(text) {
    this.sinceText = text;
    const ids = [...this.entries.keys()];
    const firstUnread = ids.findIndex((id) => this.entries.get(id).unread);
    if (firstUnread < 0) {
      this.sinceId = null;
    } else {
      let at = firstUnread;
      for (let i = firstUnread - 1; i >= 0; i--) {
        const e = this.entries.get(ids[i]);
        if (e.kind === "prose") break;
        if (e.kind === "user") { at = i; break; }
        at = i;
      }
      this.sinceId = ids[at];
    }
    this.mark();
  }
```

and, in `mark()`, after placing the selection:

```javascript
    this.list.querySelector(".row.since")?.classList.remove("since");
    const s = this.sinceId ? this.nodes.get(this.sinceId) : null;
    if (s) {
      s.classList.add("since");
      s.dataset.since = this.sinceText;
    }
```

`clear()` sets `this.sinceId = null`.

- [ ] **Step 4: The navigator moves** (`transcript.js`)

```javascript
  isProse = (n) => n.classList.contains("prose");

  message(delta) {
    if (!this.selected) this.pick();
    this.move(delta, this.isProse);
  }

  firstUnread() {
    const id = [...this.entries.keys()].find((i) => this.entries.get(i).unread);
    if (!id) return this.edge(true);
    while (!this.nodes.has(id) && this.mountEarlier());
    this.select(id);
  }

  // "2 unread · message 3 of 4": counts over every entry, not only the mounted.
  position() {
    const prose = [...this.entries.values()].filter((e) => e.kind === "prose");
    const unread = prose.filter((e) => e.unread).length;
    const at = this.selected ? prose.findIndex((e) => e.id === this.selected) : -1;
    return { index: at < 0 ? prose.length : at + 1, total: prose.length, unread };
  }
```

The jump pill becomes the navigator's last button: keep `this.jump` as that button, and instead of `this.jump.hidden = false` on new entries while scrolled up, toggle a class: `this.jump.classList.add("new")`, removed in `toBottom()` and when following resumes. The constructor's `jump.addEventListener("click", ...)` stays.

- [ ] **Step 5: Markup, wiring, keys** (`index.html`, `app.js`, `keys.js`)

`index.html`: replace `<button class="jump" id="jump" hidden>↓ latest</button>` with

```html
      <div class="nav" id="nav" hidden>
        <button class="up" id="nav-up" title="Previous agent message (Alt+↑)"></button>
        <button class="pos" id="nav-pos" title="First unread (Alt+U)"></button>
        <button class="down" id="nav-down" title="Next agent message (Alt+↓)"></button>
        <button class="latest" id="jump" title="Latest (G)"></button>
      </div>
```

`app.js`: fill the buttons with `icon("up")`, `icon("down")`, `icon("latest")` once at start; wire `nav-up` → `transcript.message(-1)`, `nav-down` → `transcript.message(1)`, `nav-pos` → `transcript.firstUnread()`; a function `drawNav()` that sets `#nav.hidden = !position().total` and `#nav-pos` to `${unread ? `${unread} unread · ` : ""}message ${index} of ${total}` — called after every transcript `snapshot`/`apply` and selection change (call it from the transcript's subscribe callbacks in `follow()` and from the key handlers below). In `follow()`'s snapshot callback, after `transcript.snapshot(entries)`, call `transcript.setSince(sinceText(sessions.get(id)))` where

```javascript
function sinceText(s) {
  const t = s?.last_read_at;
  if (!t) return "new since you left";
  const m = Math.round((Date.now() / 1000 - t) / 60);
  return `new since you left · ${m < 60 ? `${m} min` : `${Math.round(m / 60)} h`}`;
}
```

`keys.js`: three rows, scope `"session"` (the transcript's keys) so they act outside text fields, with labels and actions:

```javascript
  { scope: "session", label: "Alt+↑  Alt+↓", desc: "Previous / next agent message", action: "message", match: (ev) => alt("ArrowUp")(ev) || alt("ArrowDown")(ev) },
  { scope: "session", label: "Alt+U", desc: "First unread agent message", action: "firstUnread", match: alt("KeyU") },
```

and handlers in `app.js`'s `installKeys` map: `message(ev) { transcript.message(ev.code === "ArrowUp" ? -1 : 1); drawNav(); }`, `firstUnread() { transcript.firstUnread(); drawNav(); }` (check how existing handlers receive the event; match that signature). Add a sentence to the keys.js header comment: Alt+↑/↓ and Alt+U are not in Chrome's Linux accelerator table either.

- [ ] **Step 6: Styles** (`base.css`)

Remove the `#a2 .jump{...}` rule and add:

```css
/* the divider and the navigator */
#a2 .row.since::before{content:attr(data-since);grid-column:1/-1;display:flex;align-items:center;gap:10px;margin:6px 0 10px;font-family:var(--font-chrome);font-size:11px;color:var(--accent);background:linear-gradient(var(--accent),var(--accent)) left 50%/calc(50% - 9ch) 1px no-repeat,linear-gradient(var(--accent),var(--accent)) right 50%/calc(50% - 9ch) 1px no-repeat;justify-content:center}
#a2 .nav{position:absolute;right:14px;bottom:104px;z-index:3;display:flex;align-items:center;border:1px solid var(--rule);background:var(--raised);border-radius:999px;overflow:hidden;box-shadow:0 4px 14px rgba(0,0,0,.25);font-family:var(--font-chrome)}
#a2 .nav[hidden]{display:none}
#a2 .nav button{border:none;background:none;color:var(--strong);height:30px;padding:0 10px;cursor:pointer;display:grid;place-items:center}
#a2 .nav button+button{border-left:1px solid var(--rule)}
#a2 .nav .pos{font-size:12px;color:var(--muted)}
#a2 .nav .latest.new{color:var(--accent)}
#a2 .nav .ic{width:15px;height:15px}
#a2 .col:has(#replies:not([hidden])) .nav{bottom:150px}
```

(and delete the old `#a2 .col:has(#replies:not([hidden])) .jump{...}` rule.)

- [ ] **Step 7: Run the touched browser tests**

Run: `uv run pytest -q -m browser tests/test_browser.py -k "divider_and_navigator or j_k or alt or question_mark or long_transcript or reply_pills"`
Expected: PASS, none skipped. `test_question_mark_lists_every_key_and_escape_closes_it` must list the new rows.

- [ ] **Step 8: Commit**

```bash
git add src/aegis/client/js/transcript.js src/aegis/client/js/app.js src/aegis/client/js/keys.js src/aegis/client/index.html src/aegis/client/css/base.css tests/test_browser.py
git commit -m "feat(client): new-since-you-left divider and a navigator over agent messages, with Alt keys (#171)"
```

---

### Task 5: The ping outside the page

**Files:**
- Create: `src/aegis/client/js/ping.js`
- Modify: `src/aegis/client/js/app.js`, `src/aegis/client/index.html`, `src/aegis/client/css/base.css`
- Test: `tests/test_browser.py`

**Interfaces:**
- Consumes: cards' `attention`, `attention_line`, `title`, `handle`, `log_id`.
- Produces: `ping.js` exports `setTitle(base)`, `updatePing(metas, { onOpen })`, `installBell(button)`.

- [ ] **Step 1: Write the failing browser test**

```python
def test_the_title_favicon_and_a_notification_ping_when_a_session_needs_you(server, browser):
    errors: list = []
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    ctx.grant_permissions(["notifications"])
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.add_init_script("""
      window.__notes = [];
      window.Notification = class { constructor(t, o) { window.__notes.push([t, o]); }
        static get permission() { return 'granted'; }
        static requestPermission() { return Promise.resolve('granted'); } };
      Object.defineProperty(document, 'hidden', { get: () => window.__hidden === true });
    """)
    pg.goto(server.url)
    pg.wait_for_selector("#a2[data-view=fleet]")
    spawn(pg, "hello")
    assert not pg.title().startswith("(")
    pg.evaluate("window.__hidden = true")
    report(pg, attention="needs_you", line="Merge or rebase?", replies=[])
    pg.wait_for_function("() => document.title.startsWith('(1) ')", timeout=8000)
    assert "dot" in pg.get_attribute("#favicon", "href")
    pg.wait_for_function("() => window.__notes.length === 1", timeout=8000)
    title, opts = pg.evaluate("window.__notes[0]")
    assert opts["body"] == "Merge or rebase?"
    pg.click("#tab-fleet")
    assert pg.title().startswith("(1) Fleet")
    assert errors == []
    ctx.close()
```

(`spawn`, `report`, `turns_done` are the module's helpers; `spawn` takes a page.) The favicon's SVG data URL carries an element with `id="dot"` while the count is not zero, which the test reads through the `href`.

- [ ] **Step 2: Run it to see it fail**

Run: `uv run pytest -q -m browser tests/test_browser.py -k "title_favicon"`
Expected: FAIL waiting for the title.

- [ ] **Step 3: Implement** (`src/aegis/client/js/ping.js`)

```javascript
// The ping outside the page: the count of sessions that need you or broke, in
// the document title and on the favicon, and a desktop notification when a
// session enters one of those states while the page is hidden. The states are
// decided in Python; this only counts them and notices transitions.

const URGENT = new Set(["needs_you", "error"]);
let count = 0;
let base = "aegis";
let seen = null; // log_id -> attention, after the first draw

export function setTitle(text) {
  base = text;
  document.title = count ? `(${count}) ${base}` : base;
}

function favicon(n) {
  const dot = n ? `<circle id="dot" cx="25" cy="7" r="6" fill="#e0a872" stroke="#11100e" stroke-width="2"/>` : "";
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect x="2" y="4" width="24" height="24" rx="6" fill="#2a2721"/><text x="14" y="22" font-family="sans-serif" font-size="16" font-weight="700" text-anchor="middle" fill="#f1ede2">a</text>${dot}</svg>`;
  document.getElementById("favicon").href = `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

export function updatePing(metas, { onOpen }) {
  const n = metas.filter((m) => URGENT.has(m.attention)).length;
  if (n !== count || seen === null) {
    count = n;
    setTitle(base);
    favicon(n);
  }
  const now = new Map(metas.map((m) => [m.log_id, m.attention]));
  if (seen !== null && document.hidden && "Notification" in window && Notification.permission === "granted") {
    for (const m of metas) {
      if (!URGENT.has(m.attention) || seen.get(m.log_id) === m.attention) continue;
      const note = new Notification(`${m.title || m.handle}: ${m.attention === "error" ? "error" : "needs you"}`, {
        body: m.attention_line || "",
        tag: `${m.log_id}:${m.attention}:${m.attention_line || ""}`,
      });
      note.onclick = () => {
        window.focus();
        onOpen(m.log_id);
      };
    }
  }
  seen = now;
}

export function installBell(button) {
  const draw = () => {
    const p = "Notification" in window ? Notification.permission : "denied";
    button.dataset.state = p;
    button.title = p === "granted" ? "Desktop notifications are on" : p === "denied" ? "Notifications are blocked in this browser" : "Turn on desktop notifications";
  };
  draw();
  button.addEventListener("click", async () => {
    if ("Notification" in window && Notification.permission === "default") await Notification.requestPermission();
    draw();
  });
}
```

- [ ] **Step 4: Wiring** (`index.html`, `app.js`, `base.css`)

`index.html` head: `<link rel="icon" id="favicon" href="data:,">`. Top bar, before the `?` keys button: `<button class="keys-btn bell" id="bell" title="Turn on desktop notifications"></button>`, filled at start with `icon("bell")`. Alex asked for SVG glyphs, never emoji or Unicode marks, so add to the sprite in `glyphs.js`:
`<symbol id="g-bell" viewBox="0 0 16 16"><path d="M8 2.5a3.5 3.5 0 0 0-3.5 3.5v2.6L3.2 11h9.6l-1.3-2.4V6A3.5 3.5 0 0 0 8 2.5zM6.6 12.6a1.5 1.5 0 0 0 2.8 0" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></symbol>`.

`app.js`: `import { installBell, setTitle, updatePing } from "./ping.js";`; `$("bell").append(icon("bell"))` and `installBell($("bell"))` once; replace each `document.title = X` with `setTitle(X)` (three places); call `updatePing([...sessions.values()], { onOpen: openSession })` at the end of `onSessions()` and of `flushSessions()`.

`base.css`: `#a2 .bell[data-state=granted]{color:var(--accent)}`.

- [ ] **Step 5: Run the touched browser tests**

Run: `uv run pytest -q -m browser tests/test_browser.py -k "title_favicon or question_marks or fleet"`
Expected: PASS, none skipped.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/client/js/ping.js src/aegis/client/js/app.js src/aegis/client/index.html src/aegis/client/css/base.css tests/test_browser.py
git commit -m "feat(client): the title count, a favicon dot and desktop notifications when a session needs you (#171)"
```

---

### Task 6 (controller): docs, gates, bench, PR

- DESIGN.md: one paragraph under the attention paragraph on read state: the unread set in the meta, the channel's view versus the fold's entries, people-only reads.
- `changelog.d/171-session-attention-reading.added.md`.
- Spec: status line "slices 1 and 2 implemented"; in "What you have read", replace the `read_floor`/`read_ids` storage with the unread set and why (Ruling in the ledger).
- Gates on a clean copy, the full browser suite, the visual check in three themes, bench against `main`, PR.
