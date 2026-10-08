# aegis keyboard navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Drive the aegis web client from the keyboard: Alt chords that Chrome on Linux leaves free, plain keys outside text fields, a selection in the transcript and the Fleet, and a `?` overlay.

**Architecture:** One new module, `client/js/keys.js`, holds the table of every key and the single `keydown` listener. `app.js` supplies the actions. `Transcript` gains a selection kept by entry id; the Fleet selection is a log id kept in `app.js` and re-marked after each redraw.

**Tech Stack:** plain ES modules, no build step; pytest + Playwright browser tests against `aegis serve` with `tests/fake_claude.py`.

**Spec:** `docs/superpowers/specs/2026-10-07-aegis-keyboard-design.md`

## Global Constraints

- Alt chords match `ev.code` (`Period`, `Comma`, `BracketLeft`, `BracketRight`, `KeyN`, `Digit0`…`Digit9`), never `ev.key`, so Alt does not change what they mean.
- Plain keys match `ev.key` and act only when the event target is not `INPUT`, `TEXTAREA`, `SELECT` or contenteditable.
- Enter and Space on a focused `A`, `BUTTON` or `SUMMARY` stay native.
- Selections are held by id (entry id, log id), never by node.
- Stop, Close and rename get no key.
- No new colour: selection styling uses `--accent` and `--accent-soft`.

## Review Focus

1. A tool row selected while running loses its node on every upsert: the selection must survive it (Task 2 test).
2. j typed in the composer must be text (Task 1 test).
3. Enter on a focused summary or button must not toggle twice (Task 2: native flag, exercised by Tab then Enter).
4. Esc must keep interrupting from the composer (`test_a_session_from_spawn_to_close` already pins it).
5. Alt+1 must keep working (`test_tabs_reorder_per_browser_and_survive_a_reload` already pins it).

---

### Task 1: the key table, global chords and tab keys

**Files:**
- Create: `src/aegis/client/js/keys.js`
- Modify: `src/aegis/client/js/app.js` (the `// -- tab bar and keys` block)
- Modify: `src/aegis/client/index.html` (`tabindex="-1"` on `#tr` and `#cards`, `+` title)
- Test: `tests/test_browser.py`

**Interfaces:**
- Produces: `KEYS` (array of `{scope, label, desc, action, match, native?}`), `installKeys(actions, view)` where `view()` returns `"session" | "read" | "fleet" | "spawn" | "boot"` and `actions` maps an action name to `(ev) => void`.

- [x] **Step 1: failing tests**

```python
def focused_id(pg) -> str:
    return pg.evaluate("document.activeElement.id")


def test_alt_period_and_alt_comma_move_focus_between_composer_and_transcript(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "hello")
    page.keyboard.press("Alt+,")
    assert focused_id(page) == "tr"
    page.keyboard.press("Alt+.")
    assert focused_id(page) == "input"
    page.keyboard.type("jk")  # in a text field, plain keys are text
    assert page.input_value("#input") == "jk"
    for back in ("i", "/"):
        page.keyboard.press("Alt+,")
        page.keyboard.press(back)
        assert focused_id(page) == "input"
    assert page.input_value("#input") == "jk"
    assert page.errors == []


def test_alt_brackets_cycle_fleet_and_tabs_and_digits_pick_a_tab(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    a, b = spawn(page, "alpha"), spawn(page, "beta")
    hash_is = lambda h: page.wait_for_function("h => location.hash === h", arg=h)  # noqa: E731
    for key, want in (("Alt+]", "#fleet"), ("Alt+]", f"#s={a}"), ("Alt+[", "#fleet"), ("Alt+[", f"#s={b}")):
        page.keyboard.press(key)
        hash_is(want)
    page.keyboard.press("Alt+,")
    for key, want in (("1", f"#s={a}"), ("0", "#fleet"), ("2", f"#s={b}")):
        page.keyboard.press(key)
        hash_is(want)
    page.keyboard.press("Alt+KeyN")
    hash_is("#new")
    page.keyboard.press("Alt+.")
    assert focused_id(page) == "sp-profile"
    assert page.errors == []
```

- [x] **Step 2:** `uv run pytest -q tests/test_browser.py -k "alt_period or alt_brackets"` fails: focus stays on the input, the hash does not change.

- [x] **Step 3: `keys.js`**

```js
// The keyboard: one table of every key the client answers, and the one
// keydown listener that dispatches from it. The ? overlay is drawn from the
// same table, so it cannot list a key that does nothing.
//
// Chrome on Linux keeps Ctrl+T/W/N/Tab, Alt+1…9, Alt+←/→ and Alt+D/E/F for
// itself (chrome/browser/ui/accelerator_table.cc); the chords here are the Alt
// keys it leaves free. They match ev.code, because Alt can change ev.key.
// Plain keys act only outside text fields, as in Gmail, and the view decides
// what they do, so there is no mode to keep in your head.

const bare = (ev) => !ev.altKey && !ev.ctrlKey && !ev.metaKey;
const alt = (code) => (ev) => ev.altKey && !ev.ctrlKey && !ev.metaKey && !ev.shiftKey && ev.code === code;
const key = (...keys) => (ev) => bare(ev) && keys.includes(ev.key);

// scope: "global" acts anywhere, even while typing; "browse" outside text
// fields in any view; "session" (also the read view) and "fleet" in theirs.
export const KEYS = [
  { scope: "global", label: "Alt+.", desc: "Focus the message box", action: "composer", match: alt("Period") },
  { scope: "global", label: "Alt+,", desc: "Focus the transcript, or the Fleet cards", action: "browse", match: alt("Comma") },
  { scope: "global", label: "Alt+[  Alt+]", desc: "Previous / next tab, Fleet first", action: "cycle",
    match: (ev) => alt("BracketLeft")(ev) || alt("BracketRight")(ev) },
  { scope: "global", label: "Alt+N", desc: "New session", action: "spawn", match: alt("KeyN") },
  { scope: "global", label: "Alt+0…9", desc: "Fleet, or the n-th tab (Chrome on Linux keeps Alt+1…9)", action: "tab",
    match: (ev) => ev.altKey && /^Digit[0-9]$/.test(ev.code) },
  { scope: "global", label: "Esc", desc: "Interrupt the agent; close this list", action: "escape", match: key("Escape") },
  { scope: "browse", label: "0…9", desc: "Fleet, or the n-th tab", action: "tab", match: (ev) => bare(ev) && /^[0-9]$/.test(ev.key) },
  { scope: "browse", label: "n", desc: "New session", action: "spawn", match: key("n") },
  { scope: "browse", label: "?", desc: "This list", action: "help", match: key("?") },
];

function typing(t) {
  return t instanceof HTMLElement && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));
}

const PRESSABLE = new Set(["A", "BUTTON", "SUMMARY"]);

export function installKeys(actions, view) {
  document.addEventListener("keydown", (ev) => {
    if (ev.defaultPrevented || ev.isComposing) return;
    const v = view();
    const scopes = typing(ev.target) ? ["global"] : ["global", "browse", v === "read" ? "session" : v];
    const b = KEYS.find((k) => scopes.includes(k.scope) && k.match(ev));
    if (!b) return;
    // Enter and Space on a focused link, button or summary are the element's own.
    if (b.native && PRESSABLE.has(ev.target.tagName)) return;
    ev.preventDefault();
    actions[b.action](ev);
  });
}
```

- [x] **Step 4: wire it in `app.js`** — replace the `document.addEventListener("keydown", …)` block with:

```js
const views = () => ["#fleet", ...ordered.map((m) => `#s=${m.log_id}`)];

installKeys(
  {
    composer() {
      const v = route().view;
      if (v === "session") input.focus();
      else if (v === "spawn") $("sp-profile").focus();
    },
    browse() {
      const v = route().view;
      if (v === "session" || v === "read") $("tr").focus({ preventScroll: true });
      else if (v === "fleet") $("cards").focus({ preventScroll: true });
    },
    cycle(ev) {
      const all = views();
      const d = ev.code === "BracketRight" ? 1 : -1;
      const i = all.indexOf(location.hash || "#fleet");
      go(all[i < 0 ? (d > 0 ? 0 : all.length - 1) : (i + d + all.length) % all.length]);
    },
    spawn: () => go("#new"),
    tab(ev) {
      const n = Number(ev.altKey ? ev.code.slice(5) : ev.key);
      if (n === 0) go("#fleet");
      else if (ordered[n - 1]) go(`#s=${ordered[n - 1].log_id}`);
    },
    escape() {
      if (route().view === "session" && !editing.size) interrupt();
    },
    help() {},
  },
  () => (booted ? route().view : "boot"),
);
```

`import { installKeys } from "./keys.js";` at the top. `input` and `editing` are declared further down with `const`; the actions only read them when a key is pressed, after the module has run, so the temporal dead zone is not hit.

`index.html`: `<div class="tr" id="tr" tabindex="-1">`, `<section class="cards" id="cards" tabindex="-1">`, `title="New session (Alt+N)"` on `#tab-add`. CSS: `#a2 .tr:focus,#a2 .cards:focus{outline:none}` in `base.css`.

- [x] **Step 5:** the two new tests pass, and `-k "spawn_to_close or tabs_reorder"` still passes (Esc and Alt+1).
- [x] **Step 6:** commit `feat(client): a key table with Alt chords Chrome leaves free (#159)`.

### Task 2: selection in the transcript

**Files:**
- Modify: `src/aegis/client/js/transcript.js`, `src/aegis/client/js/keys.js`, `src/aegis/client/js/app.js`, `src/aegis/client/css/base.css`
- Test: `tests/test_browser.py`

**Interfaces:**
- Consumes: `KEYS`, `installKeys` from Task 1.
- Produces: `Transcript.selected` (entry id or null), `select(id)`, `move(delta)`, `moveTurn(delta)`, `edge(last)`, `toggle()`, `press()`, `pick()`.

- [x] **Step 1: failing tests**

```python
def selected(pg) -> str | None:
    return pg.evaluate("document.querySelector('#entries .row.sel')?.dataset.id ?? null")


def test_j_k_and_the_turn_keys_walk_the_transcript(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "one")
    page.fill("#input", "two")
    page.press("#input", "Enter")
    turns_done(page, 2)
    rows = page.evaluate("[...document.querySelectorAll('#entries .row')].map(r => r.dataset.id)")
    users = page.evaluate("[...document.querySelectorAll('#entries .row.user')].map(r => r.dataset.id)")
    page.keyboard.press("Alt+,")  # nothing selected: the last row on screen
    assert selected(page) == rows[-1]
    for k, want in (("k", rows[-2]), ("j", rows[-1]), ("j", rows[-1]), ("g", rows[0]),
                    ("K", rows[0]), ("J", users[1]), ("K", users[0]), ("ArrowDown", rows[1]),
                    ("ArrowUp", rows[0]), ("G", rows[-1])):
        page.keyboard.press(k)
        assert selected(page) == want, k
    assert page.errors == []


def test_a_selected_row_keeps_its_selection_when_it_updates_and_enter_opens_it(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    page.fill("#input", "/sleep 1")
    page.press("#input", "Enter")
    page.wait_for_selector(".row.tool.running")
    tid = page.get_attribute(".row.tool", "data-id")
    page.keyboard.press("Alt+,")
    page.keyboard.press("G")
    while selected(page) != tid:
        page.keyboard.press("k")
    turns_done(page, 1)  # the result replaced the tool row's node
    assert selected(page) == tid
    is_open = f"document.querySelector('.row[data-id=\"{tid}\"] details').open"
    page.keyboard.press("Enter")
    assert page.evaluate(is_open) is True
    page.keyboard.press(" ")
    assert page.evaluate(is_open) is False
    page.keyboard.press("Tab")  # native: the summary takes focus, and its row the selection
    assert page.evaluate("document.activeElement.tagName") == "SUMMARY"
    page.keyboard.press("Enter")  # the summary's own toggle, once
    assert page.evaluate(is_open) is True
    assert page.errors == []
```

The Tab step assumes the tool row's summary is the first focusable element after `#tr`'s current selection; if focus lands on an earlier row's summary, the assertion that matters is that the row holding focus became the selection: `selected(page) == page.evaluate("document.activeElement.closest('.row').dataset.id")`, and that one Enter flipped that row's details exactly once. Write the test that way.

- [x] **Step 2:** both fail: nothing gets `.sel`.

- [x] **Step 3: `Transcript`** — add to the class:

```js
  // -- the selection: one row, by entry id, because apply() replaces nodes ----

  mark() {
    this.list.querySelector(".row.sel")?.classList.remove("sel");
    const n = this.selected ? this.nodes.get(this.selected) : null;
    if (n) n.classList.add("sel");
    else this.selected = null;
    return n;
  }

  select(id) {
    this.selected = id;
    const n = this.mark();
    if (!n) return;
    // Keys move the selection; a focused summary in another row would take Enter.
    if (this.list.contains(document.activeElement)) this.scroller.focus({ preventScroll: true });
    n.scrollIntoView({ block: "nearest" });
  }

  // The row the reader is looking at: the last whose top is on screen.
  inView() {
    const bottom = this.scroller.getBoundingClientRect().bottom;
    const rows = [...this.list.children];
    for (let i = rows.length - 1; i >= 0; i--) if (rows[i].getBoundingClientRect().top < bottom) return rows[i];
    return null;
  }

  pick() {
    if (!this.selected) this.select(this.inView()?.dataset.id || null);
  }

  move(delta, keep = () => true) {
    let n = this.selected ? this.nodes.get(this.selected) : null;
    if (!n) return this.pick();
    do n = delta > 0 ? n.nextElementSibling : n.previousElementSibling;
    while (n && !keep(n));
    if (n) this.select(n.dataset.id);
  }

  moveTurn(delta) {
    this.move(delta, (n) => n.classList.contains("user"));
  }

  edge(last) {
    const n = last ? this.list.lastElementChild : this.list.firstElementChild;
    if (last) this.toBottom();
    if (n) this.select(n.dataset.id);
  }

  toggle() {
    const n = this.selected && this.nodes.get(this.selected);
    const d = n?.querySelector("details");
    if (!d) return;
    d.open = !d.open;
    n.dataset.touched = "1";
  }

  press() {
    const n = this.selected && this.nodes.get(this.selected);
    n?.querySelector("a.btn, button")?.click();
  }
```

In the constructor: `this.selected = null;` and

```js
    // Tab walks the rows' summaries and buttons; the row holding focus is the selection.
    list.addEventListener("focusin", (ev) => {
      const r = ev.target.closest(".row");
      if (r) {
        this.selected = r.dataset.id;
        this.mark();
      }
    });
```

At the end of `snapshot()` before `toBottom()`: `this.mark();`. In `apply()`: after the upsert loop body sets `this.nodes.set(e.id, n)`, nothing; after the whole loop, `this.mark();` (removal of the selected entry leaves `selected` pointing at a missing node, and `mark()` clears it). In `clear()`: `this.selected = null;`.

- [x] **Step 4: keys** — add to `KEYS`, after the browse rows:

```js
  { scope: "session", label: "j  ↓", desc: "Next row", action: "next", match: key("j", "ArrowDown") },
  { scope: "session", label: "k  ↑", desc: "Previous row", action: "prev", match: key("k", "ArrowUp") },
  { scope: "session", label: "J  K", desc: "Next / previous message of yours", action: "turn", match: key("J", "K") },
  { scope: "session", label: "g  G", desc: "First row / last row, and follow the tail", action: "edge", match: key("g", "G") },
  { scope: "session", label: "Enter  Space", desc: "Open or close the row's details", action: "toggle", match: key("Enter", " "), native: true },
  { scope: "session", label: "o", desc: "Press the row's first button", action: "press", match: key("o") },
  { scope: "session", label: "Tab", desc: "Walk the buttons from the selected row on", action: "none", match: () => false },
  { scope: "session", label: "i  /", desc: "Back to the message box", action: "composer", match: key("i", "/") },
```

The Tab row only documents native behaviour; `match` never fires.

In `app.js` actions: `browse()` calls `transcript.pick()` after focusing `#tr`; add

```js
    next: () => transcript.move(1),
    prev: () => transcript.move(-1),
    turn: (ev) => transcript.moveTurn(ev.key === "J" ? 1 : -1),
    edge: (ev) => transcript.edge(ev.key === "G"),
    toggle: () => transcript.toggle(),
    press: () => transcript.press(),
    none() {},
```

`base.css`: `#a2 .row.sel{box-shadow:-6px 0 0 -3px var(--accent);background:linear-gradient(var(--accent-soft),var(--accent-soft))}`, adjusted in the browser so the rule reads on all three themes.

- [x] **Step 5:** both tests pass; `-k "spawn_to_close or prompt_sent_mid_turn"` still passes.
- [x] **Step 6:** commit `feat(client): a selected row in the transcript, kept across updates (#159)`.

### Task 3: selection in the Fleet

**Files:**
- Modify: `src/aegis/client/js/app.js`, `src/aegis/client/js/keys.js`, `src/aegis/client/css/base.css`
- Test: `tests/test_browser.py`

**Interfaces:**
- Consumes: `installKeys` actions object from Task 1.
- Produces: `fleetSel` (log id or null), `fleetMark(scroll)`, `fleetMove(delta)`, `fleetOpen()` in `app.js`.

- [x] **Step 1: failing test**

```python
def test_fleet_cards_and_archive_rows_walk_with_j_and_open_with_enter(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    a, b = spawn(page, "alpha"), spawn(page, "beta")
    page.click("#close")  # b goes to the archive
    page.wait_for_selector(f"#arch-list tr[data-id='{b}']")
    sel = "document.querySelector('#cards .sel, #arch-list .sel')?.dataset.id ?? null"
    page.keyboard.press("Alt+,")
    assert page.evaluate(sel) == a
    page.keyboard.press("j")
    assert page.evaluate(sel) == b
    page.keyboard.press("Enter")
    page.wait_for_function("h => location.hash === h", arg=f"#read={b}")
    page.keyboard.press("Alt+0")
    page.wait_for_selector("#a2[data-view=fleet]")
    page.keyboard.press("k")
    assert page.evaluate(sel) == a
    page.keyboard.press("Enter")
    page.wait_for_function("h => location.hash === h", arg=f"#s={a}")
    page.keyboard.press("Alt+0")
    page.keyboard.press("/")
    assert focused_id(page) == "arch-q"
    assert page.errors == []
```

- [x] **Step 2:** fails: no `.sel` in the Fleet.

- [x] **Step 3: `app.js`**, next to the archive state:

```js
// The Fleet's selection: a card or an archive row, by log id, re-marked after
// every redraw because both are rebuilt from scratch.
let fleetSel = null;

function fleetItems() {
  return [...document.querySelectorAll("#cards .card, #arch-list tr[data-id]")];
}

function fleetMark(scroll) {
  for (const n of document.querySelectorAll("#cards .sel, #arch-list .sel")) n.classList.remove("sel");
  const n = fleetItems().find((x) => x.dataset.id === fleetSel);
  if (!n) return (fleetSel = null);
  n.classList.add("sel");
  if (scroll) n.scrollIntoView({ block: "nearest" });
}

function fleetMove(delta) {
  const items = fleetItems();
  const i = items.findIndex((x) => x.dataset.id === fleetSel);
  const n = i < 0 ? items[0] : items[i + delta];
  if (!n) return;
  fleetSel = n.dataset.id;
  fleetMark(true);
}

function fleetOpen() {
  const n = fleetItems().find((x) => x.dataset.id === fleetSel);
  if (n) go(n.classList.contains("card") ? `#s=${fleetSel}` : `#read=${fleetSel}`);
}
```

Call `fleetMark(false)` after `renderCards(...)` in `render()` and after `renderArchive(...)` in `loadArchive()`. In `browse()`, for the Fleet: `if (!fleetSel) fleetMove(1);`. Actions: `fleetNext: () => fleetMove(1)`, `fleetPrev: () => fleetMove(-1)`, `fleetOpen`, `filter: () => $("arch-q").focus()`.

`fleetMark` is a function declaration, hoisted, so `render()` can call it although it is defined further down; `fleetSel` is a `let` read only at call time, after the module ran.

- [x] **Step 4: keys**

```js
  { scope: "fleet", label: "j  ↓", desc: "Next card, then the archive", action: "fleetNext", match: key("j", "ArrowDown") },
  { scope: "fleet", label: "k  ↑", desc: "Previous card", action: "fleetPrev", match: key("k", "ArrowUp") },
  { scope: "fleet", label: "Enter", desc: "Open the session; Read an archived one", action: "fleetOpen", match: key("Enter"), native: true },
  { scope: "fleet", label: "/", desc: "Filter the archive", action: "filter", match: key("/") },
```

`base.css`: `#a2 .card.sel{outline:2px solid var(--accent);outline-offset:1px}` and `#a2 .tbl tr.sel td{background:var(--accent-soft)}`.

- [x] **Step 5:** the test passes.
- [x] **Step 6:** commit `feat(client): walk the Fleet cards and the archive with j and k (#159)`.

### Task 4: the `?` overlay

**Files:**
- Modify: `src/aegis/client/js/keys.js`, `src/aegis/client/js/app.js`, `src/aegis/client/index.html`, `src/aegis/client/css/base.css`
- Test: `tests/test_browser.py`

**Interfaces:**
- Produces: `renderKeys(box)` in `keys.js`.

- [x] **Step 1: failing test**

```python
def test_question_mark_lists_every_key_and_escape_closes_it(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    n = page.evaluate("import('/static/js/keys.js').then(m => m.KEYS.length)")
    page.keyboard.press("?")
    page.wait_for_selector("#keymap", state="visible")
    assert page.locator("#keymap tr.k").count() == n
    page.keyboard.press("Escape")
    page.wait_for_selector("#keymap", state="hidden")
    page.click("#keys-btn")
    page.wait_for_selector("#keymap", state="visible")
    page.keyboard.press("?")
    page.wait_for_selector("#keymap", state="hidden")
    assert page.errors == []
```

- [x] **Step 2:** fails: no `#keymap`.

- [x] **Step 3:** in `keys.js`:

```js
const HEADS = { global: "Anywhere", browse: "Outside a text field", session: "Transcript", fleet: "Fleet" };

export function renderKeys(box) {
  const panel = document.createElement("div");
  panel.className = "panel";
  const t = document.createElement("table");
  for (const [scope, head] of Object.entries(HEADS)) {
    const h = t.insertRow();
    h.className = "h";
    const th = document.createElement("th");
    th.colSpan = 2;
    th.textContent = head;
    h.append(th);
    for (const k of KEYS.filter((x) => x.scope === scope)) {
      const r = t.insertRow();
      r.className = "k";
      r.insertCell().textContent = k.label;
      r.insertCell().textContent = k.desc;
    }
  }
  panel.append(t);
  box.replaceChildren(panel);
}
```

`index.html`: `<button class="keys-btn" id="keys-btn" title="Keyboard (?)">?</button>` in the header before the theme picker, and `<div class="keymap" id="keymap" hidden></div>` as the last child of `#a2`.

`app.js`: `renderKeys($("keymap"))` once at load;

```js
const keymap = $("keymap");
const help = (open = keymap.hidden) => (keymap.hidden = !open);
$("keys-btn").addEventListener("click", () => help());
keymap.addEventListener("click", (ev) => ev.target === keymap && help(false));
```

The `help` action calls `help()`; `escape()` becomes `if (!keymap.hidden) help(false); else if (…) interrupt();`.

`base.css`:

```css
#a2 .keymap{position:fixed;inset:0;z-index:50;display:grid;place-items:center;background:rgba(0,0,0,.45)}
#a2 .keymap[hidden]{display:none}
#a2 .keymap .panel{background:var(--surface);border:1px solid var(--rule);border-radius:var(--r-lg);padding:14px 20px;max-height:80vh;overflow:auto;font-size:12.5px}
#a2 .keymap th{text-align:left;color:var(--faint);font-family:var(--font-chrome);font-weight:500;padding:12px 0 4px}
#a2 .keymap td{padding:3px 18px 3px 0;color:var(--ink)}
#a2 .keymap td:first-child{font-family:var(--font-mono);color:var(--strong);white-space:nowrap}
```

`.keys-btn` styled like the header's existing small controls.

- [x] **Step 4:** the test passes.
- [x] **Step 5:** commit `feat(client): ? lists every key, drawn from the key table (#159)`.

### Task 5: docs, gates, the browser, the PR

- [x] `DESIGN.md`, under *Rules that span modules*: one rule, **"A key is one row in one table"**, with the Chrome constraint and the focus-decides model.
- [x] `changelog.d/159-keyboard.added.md`.
- [x] The spec: status `implemented`, and the Fleet marking corrected to "marked in `app.js` after each redraw".
- [x] `make check` rc=0, `make test-browser` rc=0, `rift check`, `make bench` (table into the PR body).
- [x] `aegis serve` from the worktree on a spare port with a scratch root; drive it with the keys in headless Chromium, screenshot the selection and the overlay on all three themes, send the screenshots to Alex.
- [ ] Push, open the PR `Closes #159`, wait for CI.
- [ ] Alex presses the keys in his own Chrome against that server; that is the only check that sees Chrome's accelerators.
