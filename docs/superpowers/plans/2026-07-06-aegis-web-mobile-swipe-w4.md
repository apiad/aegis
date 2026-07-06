# Aegis Web Mobile + Swipe (W4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the web client a mobile-first presentation of the *same* DOM/state
— a session **list view** and a full-screen **conversation view**, with
horizontal **swipe** to move between agents — without changing the desktop UI.

**Architecture:** One CSS breakpoint (`max-width: 640px`) splits two mobile view
modes toggled by a `.conversation` class on `#app`: absent = list view (the
`#tabbar` becomes a full-screen vertical list), present = conversation view
(active pane + composer full-screen, with a back control). Desktop is unchanged
— all `.conversation`/mobile rules live inside the media query, so the class is
inert above the breakpoint. Swipe reuses the existing `cycleHandle` via a new
pure `swipeDirection` helper. Dashboards are keyboard-only and the queue strip
is hidden on mobile, so mobile is conversation-first by construction.

**Tech Stack:** Vanilla ES modules, CSS media queries, touch events, `uv`.

## Global Constraints

- No build step, no framework, no new dependencies.
- JS tests: dependency-free node scripts, `node tests/web/<name>.test.mjs`.
- Python tests (for the full gate): `uv run python -m pytest -q -m "not live"`
  (`python -m pytest`, not bare `uv run pytest`). Never pipe pytest into `tail`.
- Commit straight to **`main`** (aegis convention). TDD for the pure helper;
  CSS/DOM layout is verified by a browser smoke.
- Depends on W0–W3 (merged). Desktop layout and behavior must be **unchanged**
  above the breakpoint.
- Reuse `cycleHandle(handles, current, dir)` and the `tabs` Map order — do not
  invent a second ordering.

## File Structure

- `src/aegis/web/static/css/base.css` — **modify**: `@media (max-width: 640px)`
  block (list/conversation modes, composer pinned, dashboards hidden) +
  base `#back-btn { display: none }`.
- `src/aegis/web/static/index.html` — **modify**: `#back-btn` element.
- `src/aegis/web/static/js/app.js` — **modify**: `.conversation` toggle in
  `activateTab`, back-button wiring, swipe touch handlers.
- `src/aegis/web/static/js/tabs.js` — **modify**: add `swipeDirection`.
- Test: `tests/web/tabs.test.mjs` (**modify**, `swipeDirection` cases).

---

## Task 1: Responsive layout — list ↔ conversation + back control

**Files:**
- Modify: `src/aegis/web/static/index.html`
- Modify: `src/aegis/web/static/css/base.css`
- Modify: `src/aegis/web/static/js/app.js`

**Interfaces:**
- Produces: `#app.conversation` toggles mobile conversation view; `activateTab`
  enters it; `#back-btn` exits it. All inert on desktop (media-query-gated).

- [ ] **Step 1: Add the back control to `index.html`**

In `src/aegis/web/static/index.html`, as the first child of `<div id="app">`,
immediately after `<div id="conn-banner" hidden>…</div>`:

```html
    <button id="back-btn" type="button">‹ sessions</button>
```

- [ ] **Step 2: Add the responsive CSS**

Append to `src/aegis/web/static/css/base.css`:

```css
/* Back control: hidden on desktop and in the mobile list; shown only in the
   mobile conversation view (rule inside the media query below). */
#back-btn { display: none; background: none; border: 0; color: inherit;
            font: inherit; text-align: left; cursor: pointer;
            padding: 0.6rem 0.9rem; }

@media (max-width: 640px) {
  #app { height: 100dvh; }

  /* Dashboards / desktop-only chrome dropped on mobile v1 */
  #queuestrip { display: none !important; }
  .qd-modal, .cfg-modal, .file-modal {
    min-width: 0; width: 94vw; max-width: 94vw; }

  /* LIST VIEW (default): the tab bar becomes a full-screen vertical list. */
  #app:not(.conversation) #statusbar,
  #app:not(.conversation) #panes,
  #app:not(.conversation) #composer,
  #app:not(.conversation) #back-btn { display: none; }
  #app:not(.conversation) #tabbar {
    flex-direction: column; flex: 1; overflow-y: auto;
    gap: 0.25rem; padding: 0.5rem; }
  #app:not(.conversation) #tabbar .chip {
    width: 100%; box-sizing: border-box; padding: 0.9rem; font-size: 1.05rem; }

  /* CONVERSATION VIEW */
  #app.conversation #tabbar { display: none; }
  #app.conversation #back-btn { display: block; }
  #app.conversation #composer { position: sticky; bottom: 0; }
}
```

- [ ] **Step 3: Toggle the view in `app.js`**

In `activateTab(handle)` (after `activeHandle = handle;`), enter conversation
view (inert on desktop):

```js
  document.getElementById("app").classList.add("conversation");
```

Wire the back control near `boot()` (e.g. inside `wireKeys` or a new
`wireMobile()` called from `boot`). Add:

```js
function wireMobile() {
  const back = document.getElementById("back-btn");
  if (back) back.addEventListener("click", () => {
    document.getElementById("app").classList.remove("conversation");
  });
}
```

And call `wireMobile();` in `boot()` alongside `wireComposer(); wireKeys();`.

- [ ] **Step 4: Browser smoke (view toggle logic, width-independent)**

With `aegis web` running, in the browser console (or via saidkick `exec`):

```js
// simulate a tap activating a tab, then back
app.classList.contains("conversation")   // after activateTab → true
// click #back-btn → app.classList.contains("conversation") === false
```

Verify: `activateTab` adds `.conversation`; `#back-btn` removes it. Then narrow
the window < 640px and confirm the list fills the screen, tapping a row opens the
conversation full-screen with the composer pinned, and back returns to the list.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/web/static/index.html src/aegis/web/static/css/base.css src/aegis/web/static/js/app.js
git commit -m "feat(web): mobile-first list/conversation views + back control"
```

---

## Task 2: Swipe between agents

**Files:**
- Modify: `src/aegis/web/static/js/tabs.js` (`swipeDirection`)
- Modify: `src/aegis/web/static/js/app.js` (touch handlers)
- Test: `tests/web/tabs.test.mjs`

**Interfaces:**
- Produces: `swipeDirection(dx, dy, threshold=60) -> -1 | 0 | 1` — `+1`
  (next) for a leftward swipe, `-1` (prev) for rightward, `0` when the gesture
  is too short or vertical-dominant (so it never steals transcript scrolling).

- [ ] **Step 1: Write the failing test**

Add to `tests/web/tabs.test.mjs` (import `swipeDirection` in the existing import
block):

```js
// swipeDirection — horizontal-dominant gestures pick a direction
{
  assert.equal(swipeDirection(-100, 5), 1);    // swipe left → next
  assert.equal(swipeDirection(100, -5), -1);   // swipe right → prev
  assert.equal(swipeDirection(-20, 0), 0);     // too short
  assert.equal(swipeDirection(-100, -120), 0); // vertical-dominant → ignore
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `node tests/web/tabs.test.mjs`
Expected: FAIL — `swipeDirection is not a function`.

- [ ] **Step 3: Add `swipeDirection` to `tabs.js`**

Append to `src/aegis/web/static/js/tabs.js`:

```js
// Classify a touch gesture into a tab direction. +1 = next (swipe left),
// -1 = prev (swipe right), 0 = ignore (too short, or vertical-dominant so
// transcript scrolling is never hijacked).
export function swipeDirection(dx, dy, threshold = 60) {
  if (Math.abs(dx) < threshold || Math.abs(dx) <= Math.abs(dy)) return 0;
  return dx < 0 ? 1 : -1;
}
```

- [ ] **Step 4: Run the test green**

Run: `node tests/web/tabs.test.mjs`
Expected: prints its passing line.

- [ ] **Step 5: Wire the touch handlers in `app.js`**

Import the helper (extend the existing `tabs.js` import):

```js
import { reconcileTabs, cycleHandle, gotoHandle, swipeDirection } from "./tabs.js";
```

Add near the other `panesEl` wiring:

```js
let _touchX = 0, _touchY = 0;
panesEl.addEventListener("touchstart", (e) => {
  const t = e.changedTouches[0]; _touchX = t.clientX; _touchY = t.clientY;
}, { passive: true });
panesEl.addEventListener("touchend", (e) => {
  const t = e.changedTouches[0];
  const dir = swipeDirection(t.clientX - _touchX, t.clientY - _touchY);
  if (!dir) return;
  const next = cycleHandle([...tabs.keys()], activeHandle, dir);
  if (next && next !== activeHandle) activateTab(next);
}, { passive: true });
```

- [ ] **Step 6: Browser smoke**

On a phone (or device-emulation), in the conversation view with ≥2 agents,
swipe left/right and confirm the foreground agent changes to the next/prev
session; a vertical drag still scrolls the transcript.

- [ ] **Step 7: Commit**

```bash
git add src/aegis/web/static/js/tabs.js src/aegis/web/static/js/app.js tests/web/tabs.test.mjs
git commit -m "feat(web): swipe between agents in the mobile conversation view"
```

---

## Task 3: Gate + CHANGELOG

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Run the JS tests**

Run: `for f in tests/web/*.test.mjs; do node "$f" || exit 1; done; echo ALL_JS_OK`
Expected: `ALL_JS_OK`.

- [ ] **Step 2: Run the web Python tests**

Run: `uv run python -m pytest tests/test_web_*.py -q; echo "EXIT=$?"`
Expected: `EXIT=0` (no Python touched here; this just confirms no regression).

- [ ] **Step 3: CHANGELOG**

Under `## [Unreleased]`:

```markdown
- Mobile-first web layout: below 640px the client shows a session **list** and
  a full-screen **conversation** view (same DOM/state), with the composer
  pinned above the keyboard and a back control. **Swipe** left/right moves
  between agents. Desktop is unchanged; dashboards are desktop/TUI-only on
  mobile v1.
```

- [ ] **Step 4: Commit + push**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): mobile-first layout + swipe between agents"
git push origin main
```

---

## Self-Review notes (for the executor)

- **Spec coverage (W4):** list↔conversation + composer-above-keyboard + back =
  Task 1; swipe-between-agents = Task 2; dashboards hidden on mobile = the
  `#queuestrip`/modal rules in Task 1 (dashboards are otherwise keyboard-only,
  so unreachable on mobile by construction).
- **Desktop untouched:** every mobile rule is inside `@media (max-width: 640px)`;
  `activateTab` adds a class that only that media query reads. Confirm the
  desktop tab bar / panes / modals are visually identical after Task 1.
- **Swipe vs. scroll:** `swipeDirection` returns 0 unless the gesture is
  horizontal-dominant and past `threshold`, and the listeners are `passive`, so
  vertical transcript scrolling is never blocked.
- **Testability:** the pure `swipeDirection` is node-tested; the CSS/DOM layout
  and the touch interaction are browser-smoke only (no jsdom). Land Task 1's
  smoke before wiring swipe.
- **Known v1 limitation:** on mobile, `activateTab` always enters conversation
  view, so an auto-activated boot lands in the conversation rather than the
  list; back reaches the list. Fine for v1 — revisit if landing-on-list is
  wanted.
