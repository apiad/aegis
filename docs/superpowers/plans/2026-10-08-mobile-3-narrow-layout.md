# Mobile slice 3: the narrow layout, implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The desktop page works on a 390 px phone with touch: tabs reachable, the side panel as a drawer, 44 px targets, Enter adding a line, aegis's own dialogs, and the interrupt beside send.

**Architecture:** The markup stays the desktop's. One CSS block below 760 px and one under `(pointer: coarse)` adapt it; a ☰ button toggles `data-side=open` on `#a2`, which turns the existing `<aside class="side">` into a drawer. A small `js/dialog.js` replaces the two `confirm()` calls, and a test bans native dialogs. The composer's text Stop goes; an icon interrupt sits beside send, and the side panel gains Restart.

**Tech Stack:** plain CSS and ES modules; pytest + Playwright (touch emulation makes `(pointer: coarse)` match: checked with `has_touch=True`).

**Spec:** `docs/superpowers/specs/2026-10-08-mobile-and-pwa-design.md`, section 2, "The narrow layout". The approved prototype is `.playground/aegis-mobile/narrow.css` in the workspace.

## Global Constraints

- Below 760 px is the narrow layout (the breakpoint already in `base.css:190`); touch sizing follows `(pointer: coarse)` at any width.
- Touch targets: reply chips, send, interrupt and `.btn` at least 44 px tall; tabs and tool row summaries at least 40 px.
- The drawer is `min(330px, 88vw)` wide.
- Tabs on a phone hide their handle and cut the title at 16 characters.
- Enter in the composer adds a line under `(pointer: coarse)`; the new-session form keeps Enter-to-start.
- No `confirm(`, `alert(` or `prompt(` anywhere in `src/aegis/client/js/`.
- Every key the client answers is a row in `KEYS` (`js/keys.js`); Esc reaches dialogs and the drawer through the existing `escape` action.
- No element changes on a desktop with a mouse above 760 px, except: the text Stop under the composer becomes the ■ button beside send, the side panel gains Restart, and Close asks through the aegis dialog.

## Review Focus

- A tap on the dimmed transcript while the drawer is open: it must close the drawer and must not also open a row, follow a link or focus the composer behind it. Pinned in Task 3 (the backdrop takes the click).
- Esc with the dialog open over a working session: it cancels the dialog and does not interrupt the turn. Pinned in Task 1.
- A session that starts working while the drawer is open: the drawer's Stop, Restart and Close stay usable, and Restart is disabled while it works. Pinned in Task 2.
- A phone rotated to landscape (844 × 390): wider than 760 px, so the desktop layout returns, and the touch targets stay large. Pinned in Task 3 by a landscape case.
- A long reply chip on a phone (over 120 characters): it wraps inside its full-width button instead of widening the page. Pinned in Task 3's no-overflow check with a long chip.

---

### Task 1: aegis's own dialog

**Files:**
- Create: `src/aegis/client/js/dialog.js`
- Modify: `src/aegis/client/js/app.js` (`sendLine` line 778; the `close` listener line 893; the `escape` action line 721)
- Modify: `src/aegis/client/js/keys.js` (the Esc row's `desc`)
- Modify: `src/aegis/client/css/base.css` (dialog rules next to `.keymap`)
- Create: `tests/test_client_rules.py`
- Modify: `tests/test_browser.py` (helper `close_session`; the four `page.click("#close")` sites at lines 340, 463, 489, 1133; `new_page` keeps `pg.on("dialog", ...)` as a guard)

**Interfaces:**
- Produces: `ask(question: string, { ok = "OK", cancel = "Cancel" } = {}) => Promise<boolean>`, `cancelAsk() => boolean` (true when a dialog was open and is now cancelled), exported from `js/dialog.js`. Markup: `<div class="dialog" id="dialog">` appended to `#a2`, with `.q`, `button.ok`, `button.cancel`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_client_rules.py`:

```python
"""Rules over the client's source that a test can hold without a browser."""

import re
from pathlib import Path

JS = Path(__file__).parents[1] / "src" / "aegis" / "client" / "js"


def test_the_client_never_opens_a_native_dialog():
    """A native confirm, alert or prompt looks foreign in the installed app and
    blocks the page; the client asks through js/dialog.js."""
    found = [
        f"{p.name}:{n}"
        for p in JS.glob("*.js")
        for n, line in enumerate(p.read_text().splitlines(), 1)
        if re.search(r"(?:^|[^\w.]|window\.)(confirm|alert|prompt)\(", line)
    ]
    assert found == []
```

In `tests/test_browser.py`, add after `tab_ids`:

```python
def close_session(pg) -> None:
    """Close the shown session through the aegis dialog."""
    pg.click("#close")
    pg.wait_for_selector("#dialog .ok", state="visible")
    pg.click("#dialog .ok")
```

and replace `page.click("#close")` with `close_session(page)` at lines 340, 463, 489 and 1133. Append:

```python
def test_close_asks_in_an_aegis_dialog_and_esc_cancels_without_interrupting(
    server, page
):
    native: list = []
    page.on("dialog", lambda d: native.append(d.message))
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    a = spawn(page)
    page.fill("#input", "/sleep 3")
    page.press("#input", "Enter")
    page.wait_for_selector(".row.tool.running")
    page.click("#close")
    page.wait_for_selector("#dialog .ok", state="visible")
    assert "Close" in page.inner_text("#dialog .q")
    page.keyboard.press("Escape")
    page.wait_for_selector("#dialog", state="hidden")
    assert page.is_visible(".row.tool.running"), "Esc on the dialog interrupted"
    assert tab_ids(page) == [a]
    page.fill("#input", "/close")
    page.press("#input", "Enter")
    page.click("#dialog .cancel")
    assert tab_ids(page) == [a]
    close_session(page)
    page.wait_for_selector("#a2[data-view=fleet]")
    assert tab_ids(page) == [] and native == [] and page.errors == []
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest -q tests/test_client_rules.py && uv run pytest -q -m browser -k "aegis_dialog"`
Expected: FAIL: `['app.js:778', 'app.js:896']`, and the browser test times out waiting for `#dialog .ok`.

- [ ] **Step 3: Implement `dialog.js`**

```js
// aegis's own confirm: a question over the dimmed page and two buttons, drawn
// like the ? list. The browser's confirm() looks foreign in the installed app
// and blocks the page (tests/test_client_rules.py bans it). Enter on the
// focused OK is the button's own; Esc reaches cancelAsk() through the escape
// action in keys.js; a tap outside cancels.

let open = null; // the pending answer's resolve

function box() {
  let d = document.getElementById("dialog");
  if (d) return d;
  d = document.createElement("div");
  d.className = "dialog";
  d.id = "dialog";
  d.hidden = true;
  d.innerHTML =
    '<div class="panel" role="dialog" aria-modal="true"><p class="q"></p>' +
    '<div class="acts"><button class="btn cancel"></button><button class="btn primary ok"></button></div></div>';
  d.addEventListener("click", (ev) => ev.target === d && answer(false));
  d.querySelector(".ok").addEventListener("click", () => answer(true));
  d.querySelector(".cancel").addEventListener("click", () => answer(false));
  document.getElementById("a2").append(d);
  return d;
}

function answer(yes) {
  const resolve = open;
  open = null;
  box().hidden = true;
  resolve?.(yes);
}

export function ask(question, { ok = "OK", cancel = "Cancel" } = {}) {
  if (open) answer(false);
  const d = box();
  d.querySelector(".q").textContent = question;
  d.querySelector(".ok").textContent = ok;
  d.querySelector(".cancel").textContent = cancel;
  d.hidden = false;
  d.querySelector(".ok").focus();
  return new Promise((resolve) => (open = resolve));
}

export function cancelAsk() {
  if (!open) return false;
  answer(false);
  return true;
}
```

- [ ] **Step 4: Use it in `app.js` and `keys.js`**

Import: `import { ask, cancelAsk } from "./dialog.js";`

Add near `sendLine`:

```js
const askClose = (s) =>
  ask(`Close ${s.title || s.handle}? Its tab goes away in every browser; it stays in the archive.`, { ok: "Close" });
```

In `sendLine`, replace line 778 with `if (text === "/close" && !(await askClose(s))) return;`. In the `close` listener, replace the `confirm(...)` line with `if (!(await askClose(s))) return;`.

In the `escape` action, make the dialog first:

```js
    escape() {
      if (cancelAsk()) return;
      if (!keymap.hidden) help(false);
      else if (closeMonitorCard()) return;
      else if (route().view === "session" && !editing.size) interrupt();
    },
```

In `keys.js`, change the Esc row's `desc` to `"Interrupt the agent; close a dialog or this list"`.

In `base.css`, after the `.keymap` rules:

```css
#a2 .dialog{position:fixed;inset:0;z-index:60;display:grid;place-items:center;background:rgba(0,0,0,.45);padding:16px}
#a2 .dialog[hidden]{display:none}
#a2 .dialog .panel{background:var(--surface);border:1px solid var(--rule);border-radius:var(--r-lg);padding:18px 20px;max-width:420px;width:100%}
#a2 .dialog .q{margin:0 0 16px;color:var(--strong);font-size:14px;line-height:1.45}
#a2 .dialog .acts{display:flex;gap:10px;justify-content:flex-end}
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest -q tests/test_client_rules.py && uv run pytest -q -m browser -k "aegis_dialog or spawn_to_close or close_in_one or reopen_from or question_mark"`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/client/js/dialog.js src/aegis/client/js/app.js src/aegis/client/js/keys.js src/aegis/client/css/base.css tests/test_client_rules.py tests/test_browser.py
git commit -m "feat(client): aegis asks in its own dialog, never the browser's"
```

### Task 2: Interrupt beside send; Stop, Restart and Close in the side panel

**Files:**
- Modify: `src/aegis/client/index.html` (the composer's `.acts`, the `.chips` row, the side panel's `.sec.acts.live-only`)
- Modify: `src/aegis/client/js/app.js` (`renderMeta` lines 372-375; the `stop` listener line 881; new `restart` listener)
- Modify: `src/aegis/client/css/base.css` (`.composer .acts .int`)
- Test: `tests/test_browser.py` (append)

**Interfaces:**
- Produces: `#interrupt` (icon button in `.composer .acts`, hidden unless working), `#restart` (side panel, disabled while working). `#stop` is removed.

- [ ] **Step 1: Write the failing test**

```python
def test_the_interrupt_sits_beside_send_and_restart_sends_continue(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    assert page.locator("#stop").count() == 0, "the text Stop under the box is gone"
    assert page.is_hidden("#interrupt")
    page.fill("#input", "/sleep 5")
    page.press("#input", "Enter")
    page.wait_for_selector(".row.tool.running")
    box = page.locator(".composer .box").bounding_box()
    btn = page.locator("#interrupt").bounding_box()
    assert box["y"] <= btn["y"] and btn["y"] + btn["height"] <= box["y"] + box["height"]
    assert page.is_disabled("#restart")
    page.click("#interrupt")
    turns_done(page, 1)
    assert page.is_hidden("#interrupt")
    page.click("#restart")
    page.wait_for_selector(".row.user >> text=Continue")
    turns_done(page, 2)
    assert page.errors == []
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest -q -m browser -k interrupt_sits_beside`
Expected: FAIL on `#stop` still existing.

- [ ] **Step 3: Implement**

`index.html`, the composer box's actions:

```html
          <div class="acts"><button class="int" id="interrupt" title="Interrupt (Esc)" aria-label="Interrupt" hidden>■</button><button class="send" id="send" title="Send (Enter)" aria-label="Send">↵</button></div>
```

Delete `<button class="btn" id="stop" hidden>Stop</button>` from `.chips`. The side panel's actions:

```html
      <div class="sec acts live-only"><button class="btn" id="stop-session" title="End the process; the next message resumes it">Stop</button><button class="btn" id="restart" title="Send “Continue”">Restart</button><button class="btn" id="close" title="Archive it: the tab goes away in every browser">Close</button></div>
```

`app.js`, in `renderMeta`: replace `$("stop").hidden = !working;` with

```js
  $("interrupt").hidden = !working;
  $("restart").disabled = working;
```

Replace `$("stop").addEventListener("click", interrupt);` with

```js
$("interrupt").addEventListener("click", interrupt);
// A nudge after an interrupt, an error or a stop: a message to a stopped
// session resumes it, so Restart is one sentence to the agent.
$("restart").addEventListener("click", () => sendLine("Continue", false));
```

`base.css`, after `#a2 .composer .acts .send{...}`:

```css
#a2 .composer .acts .int{background:none;border:1px solid var(--rule);border-radius:6px;color:var(--err);font-size:12px;line-height:1;padding:7px 9px;cursor:pointer}
#a2 .composer .acts .int[hidden]{display:none}
```

Check that `grep -n '"stop"' src/aegis/client/js/*.js` prints nothing (keys.js reaches `interrupt()` directly, not the button).

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q -m browser -k "interrupt_sits_beside or send_sits_inside or spawn_to_close"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/client/index.html src/aegis/client/js/app.js src/aegis/client/css/base.css tests/test_browser.py
git commit -m "feat(client): interrupt beside send; Stop, Restart and Close in the side panel"
```

### Task 3: The narrow layout and touch

**Files:**
- Modify: `src/aegis/client/index.html` (viewport meta; `#side-btn` in the header)
- Modify: `src/aegis/client/css/base.css` (`#a2{height:100vh}` at line 17 becomes `100dvh`; replace the `@media (max-width:760px)` block at lines 190-193; add the `(pointer: coarse)` block)
- Modify: `src/aegis/client/js/app.js` (the ☰ toggle, the backdrop, closing the drawer in `follow` and in the `escape` action, Enter on touch, the placeholder)
- Test: `tests/test_browser.py` (append)

**Interfaces:**
- Produces: `#side-btn`; `#a2[data-side=open]` while the drawer is open.

- [ ] **Step 1: Write the failing tests**

```python
def phone(browser, errors: list, landscape: bool = False):
    size = {"width": 844, "height": 390} if landscape else {"width": 390, "height": 844}
    pg = browser.new_context(viewport=size, has_touch=True, is_mobile=True).new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))
    return pg


WIDER = """[...document.querySelectorAll('#a2 *')].filter(e => {
  const r = e.getBoundingClientRect();
  return r.width > 0 && (r.left < -1 || r.right > innerWidth + 1) && !e.closest('.tablist, .side, pre, .diff');
}).map(e => e.className || e.tagName).slice(0, 5)"""


def test_a_phone_reaches_tabs_the_drawer_and_the_chips(server, browser):
    errors: list = []
    page = phone(browser, errors)
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    a = spawn(page, "first")
    b = spawn(page, "second")
    assert page.evaluate(WIDER) == []
    page.tap(f"#tablist .tab[data-id='{a}']")
    page.wait_for_function("id => location.hash === '#s=' + id", arg=a)
    assert page.locator(".side").bounding_box()["x"] >= 389, "the drawer starts closed"
    page.tap("#side-btn")
    page.wait_for_function("document.getElementById('a2').dataset.side === 'open'")
    page.wait_for_timeout(250)  # the slide
    assert page.locator(".side").bounding_box()["x"] < 390 - 300
    assert page.is_visible("#restart") and page.is_visible("#close")
    page.mouse.click(20, 400)  # the dimmed transcript
    page.wait_for_function("document.getElementById('a2').dataset.side !== 'open'")
    page.tap("#side-btn")
    page.tap(f"#tablist .tab[data-id='{b}']")
    page.wait_for_function("document.getElementById('a2').dataset.side !== 'open'")
    # Enter adds a line on a touch screen; the button sends.
    page.tap("#input")
    page.keyboard.type("one")
    page.keyboard.press("Enter")
    page.keyboard.type("two")
    assert page.input_value("#input") == "one\ntwo"
    page.tap("#send")
    turns_done(page, 2)
    # Reply chips: one per row, 44 px or taller, and a long one wraps.
    page.evaluate(
        """(() => { const r = document.getElementById('replies'); r.hidden = false;
        r.innerHTML = '<span class=lbl>reply</span>' + ['Yes', 'No', 'x '.repeat(80)]
          .map(t => '<button class=rp>' + t + '</button>').join(''); })()"""
    )
    chips = page.eval_on_selector_all(".rp", "bs => bs.map(b => b.getBoundingClientRect().toJSON())")
    assert all(c["height"] >= 44 for c in chips)
    assert len({round(c["x"]) for c in chips}) == 1 and chips[0]["width"] > 300
    assert page.evaluate(WIDER) == []
    assert errors == []


def test_a_phone_in_landscape_gets_the_desktop_layout_with_touch_targets(
    server, browser
):
    errors: list = []
    page = phone(browser, errors, landscape=True)
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    assert page.is_visible(".side") and page.is_hidden("#side-btn")
    assert page.locator("#send").bounding_box()["height"] >= 44
    assert errors == []
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest -q -m browser -k "a_phone"`
Expected: FAIL: the tap on a tab times out (the Fleet tab covers it), as measured on `main`.

- [ ] **Step 3: Implement the markup**

`index.html`: the viewport tag becomes

```html
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, interactive-widget=resizes-content">
```

and the header gains, right before `<button class="keys-btn" ...>`:

```html
    <button class="side-btn" id="side-btn" title="Session details" aria-label="Session details">☰</button>
```

- [ ] **Step 4: Implement the CSS**

In `base.css` line 17 change `height:100vh` to `height:100dvh`. Replace the block at lines 190-193

```css
@media (max-width:760px){
  #a2[data-view=session] .v-session{grid-template-columns:1fr}
  #a2 .side{display:none}
}
```

with

```css
#a2 .side-btn{display:none}
/* A phone: the desktop markup, wrapped. Tabs take their own scrolling row, the
   side panel slides in as a drawer (data-side=open), the band stacks. */
@media (max-width:760px){
  #a2 .tabs{flex-wrap:wrap;padding:4px 6px 0;padding-top:max(4px,env(safe-area-inset-top))}
  #a2 .tablist{order:9;flex:1 0 100%;margin:2px -6px 0;padding:0 6px;border-top:1px solid var(--rule)}
  #a2 .tabs .servers{padding:0 6px}
  #a2 .tabs .ver-top{display:none}
  #a2 .tablist .tab .srv{display:none}
  #a2 .tablist .tab .tname{max-width:16ch}
  #a2 .theme-pick{font-size:0;gap:0}
  #a2 .keys-btn{display:none}
  #a2[data-view=session] .side-btn{display:block;background:none;border:0;color:var(--muted);font-size:18px;min-width:44px;cursor:pointer}
  #a2[data-view=session] .v-session{grid-template-columns:minmax(0,1fr)}
  #a2 .side{position:fixed;top:0;right:0;bottom:0;width:min(330px,88vw);z-index:30;
    transform:translateX(100%);transition:transform .18s ease;box-shadow:-8px 0 24px rgba(0,0,0,.5);
    padding-top:max(16px,env(safe-area-inset-top));padding-bottom:max(16px,env(safe-area-inset-bottom))}
  #a2[data-side=open] .side{transform:none}
  #a2[data-side=open] .v-session:after{content:"";position:fixed;inset:0;z-index:29;background:rgba(0,0,0,.5)}
  #a2 .tr{padding:12px 12px 6px}
  #a2 .tr:before{left:22px}
  #a2 .composer{padding:6px 8px max(8px,env(safe-area-inset-bottom))}
  #a2 .replies{flex-direction:column;align-items:stretch}
  #a2 .replies .lbl{display:none}
  #a2 .jump{right:14px}
  #a2 .band{grid-template-columns:minmax(0,1fr);gap:14px;padding:12px 14px}
  #a2 .gauge{grid-template-columns:62px minmax(0,1fr) max-content;gap:8px}
  #a2 .cards{padding:12px;gap:10px}
  #a2[data-view=spawn] .v-spawn{padding:16px 10px;place-items:start stretch}
}
@media (prefers-reduced-motion:reduce){#a2 .side{transition:none}}
/* Touch, at any width: 44 px targets. */
@media (pointer:coarse){
  #a2 .rp{min-height:44px;padding:10px 14px;font-size:14.5px}
  #a2 .composer .acts .send,#a2 .composer .acts .int{min-width:44px;min-height:44px}
  #a2 .tab{min-height:40px}
  #a2 .row.tool details>summary{padding:9px 0}
  #a2 .btn{min-height:44px}
}
```

- [ ] **Step 5: Implement the JS**

In `app.js`:

```js
// -- the drawer: the side panel on a phone (base.css, max-width 760px) --------
const closeSide = () => delete root.dataset.side;
$("side-btn").addEventListener("click", () => {
  if (root.dataset.side === "open") closeSide();
  else root.dataset.side = "open";
});
// The dimmed transcript is the session view's own ::after, so a tap on it
// lands on the view itself and goes no further.
document.querySelector(".v-session").addEventListener("click", (ev) => {
  if (ev.target === ev.currentTarget && root.dataset.side === "open") closeSide();
});
```

In `follow`, as its first line after `if (shown === id) return;`, add `closeSide();`. In the `escape` action, after `if (cancelAsk()) return;`, add `if (root.dataset.side === "open") return closeSide();`.

Enter on touch: change the composer's keydown listener to

```js
// On a touch screen Enter adds a line and the button sends: the key sits where
// a mistap lands, and half a message costs a turn.
const touch = matchMedia("(pointer: coarse)");
input.addEventListener("keydown", (ev) => {
  if (menu.onKey(ev)) return;
  if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing && !touch.matches) {
    ev.preventDefault();
    send();
  }
});
```

and in `renderMeta`, make the live placeholder say so:

```js
      : touch.matches
        ? "Message the agent. ↵ sends, / for commands."
        : "Message the agent. Enter sends, Shift+Enter adds a line, / for commands, Esc interrupts.";
```

(`touch` must be declared before `renderMeta` first runs; put the `const touch = ...` line at the top of the composer section, before `function autosize()`.)

- [ ] **Step 6: Run the tests**

Run: `uv run pytest -q -m browser -k "a_phone or interrupt_sits or aegis_dialog or message_box or send_sits"`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/aegis/client/index.html src/aegis/client/css/base.css src/aegis/client/js/app.js tests/test_browser.py
git commit -m "feat(client): the desktop page on a phone: wrapped tabs, the side panel as a drawer, touch targets"
```

### Task 4: DESIGN.md, release notes, and a real check

**Files:**
- Modify: `DESIGN.md` ("A key is one row in one table"; new "One page for every screen")
- Create: `changelog.d/mobile-narrow-layout.added.md`, `changelog.d/mobile-composer-actions.changed.md`

- [ ] **Step 1: DESIGN.md**

Add after "A theme is one CSS file over one markup":

```markdown
**One page for every screen.** A phone gets the desktop's markup. Below 760 px
one CSS block wraps the tab bar onto its own row, turns the side panel into a
drawer (`data-side=open`, opened by ☰) and stacks the Fleet band; under
`(pointer: coarse)` touch targets grow to 44 px and Enter in the composer adds a
line. A second set of screens would be a second client to keep in step. The
client asks through its own dialog (`js/dialog.js`), never the browser's:
`tests/test_client_rules.py` fails on `confirm(`, `alert(` or `prompt(`.
```

In "A key is one row in one table", append: `Esc closes, in order, a dialog, the drawer, the ? list and a monitor card, and only then interrupts.`

- [ ] **Step 2: Release notes**

`changelog.d/mobile-narrow-layout.added.md`:

```markdown
- **aegis works on a phone.** Below 760 px the tabs get their own row that scrolls sideways, ☰ opens the session's side panel as a drawer, and the Fleet band stacks. On a touch screen the reply chips, buttons and tool rows are at least 40 to 44 px tall, and Enter in the message box adds a line while ↵ sends.
```

`changelog.d/mobile-composer-actions.changed.md`:

```markdown
- **Interrupt is the ■ beside send, and the side panel has Restart.** The text Stop under the message box is gone. Restart sends "Continue", which also resumes a stopped session. Close now asks in aegis's own dialog.
```

Run: `make changelog-check`
Expected: exit 0.

- [ ] **Step 3: The gates and a real browser**

Run: `make check`, then `uv run pytest -q -m browser`.
Expected: all pass. Then start `uv run aegis serve --root <a temp dir> --port 8795 -d` from this worktree and open it in Chrome's device toolbar (Pixel 7, touch on): tap through the tabs, open and close the drawer, close a session through the dialog, type two lines with Enter, send with ↵. `kill` the server.

- [ ] **Step 4: Commit**

```bash
git add DESIGN.md changelog.d/mobile-narrow-layout.added.md changelog.d/mobile-composer-actions.changed.md
git commit -m "docs: one page for every screen, and aegis's own dialog"
```
