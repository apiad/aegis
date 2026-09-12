# Session Propagation Implementation Plan (closes stage 4's xfail)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans
> to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A tab opened in one view appears in every other view over the same
brain, and closing it closes it everywhere — so `tests/views/test_multi_view.py
::test_a_session_opened_in_one_view_appears_in_the_other` passes and its
`xfail(strict=True)` marker comes off.

**Why this is its own plan, before stage 5.** Stage 4 built N views over one
brain and proved four of the spec's five properties. The fifth is unbuilt:
sessions do not cross. Building it after the transport exists means every
failure has two candidate causes; building it now keeps one.

**Spec:** `docs/superpowers/specs/2026-09-07-retire-web-ui-tui-over-web-design.md`
**Predecessor:** `docs/superpowers/plans/2026-09-11-aegis-view-seam.md` (executed)

## Global Constraints

- **Python ≥ 3.13**, `uv` only. **English** everywhere. **Conventional commits**,
  one per task. **Never `git add -A`** — shared checkout.
- **Baseline is 3629 passed, 1 skipped, 1 xfailed, rc=0** on
  `uv run pytest -q -m "not live"`. Any red is a regression.
- **`asyncio_mode = "auto"`** — async tests need no marker.
- **The single-view path changes its spawn route, deliberately.** Stage 4's
  plan called that constraint absolute; Alex relaxed it on 2026-09-11 for
  exactly this task. One spawn route is the point of the brain/view split —
  two is how they drift. Everything a user can *observe* about `aegis` must
  still be identical, and the full suite is the evidence.

## Facts measured against the tree at `c1063e2`

Read or executed, not inferred.

| Fact | Where |
|---|---|
| `ConversationPane(core=…)` already bypasses the `AgentSession` wrapping — added for remote mode's `RemotePaneCore` | `tui/pane.py:110-113` |
| A real `AgentSession` **instance** satisfies all 19 names the pane reads off `_core` (`send`, `state`, `title`, `metrics`, `place`, `on_loop`, `rehydrate_plan`, …) — measured by construction, not by `hasattr` on the class | measured 2026-09-11 |
| `AgentSession` keeps observer **lists** on all five channels, so N panes over one session is already supported | `core/session.py:206-210`, `:235-253` |
| `SessionManager` has **no** session-added hook — the only observers it installs are the per-session event log | `core/manager.py:251-253` |
| `SessionManager._sync_spawn` appends to `self._sessions` and returns the `AgentSession` | `core/manager.py:249`, `:257` |
| `SessionManager.close` removes from `self._sessions` | `core/manager.py:470` |
| `_SessionManagerAdapter.spawn` builds **both** the pane and its session, then appends to `app._panes` | `tui/app.py:2407-2441` |
| `AegisApp.spawn` delegates to that adapter and returns `sess.handle` | `tui/app.py:1812-1828` |
| **`AegisApp.__init__` always builds its own `HandleRegistry()`**, even with a bridge | `tui/app.py:388` |
| `AegisApp` mints through `self._handles.mint(...)` | `tui/app.py:869` |
| `SessionManager` mints through `self.handles.mint({s.handle for s in self._sessions})` | `core/manager.py:218` |
| The local plane sets no `_remote_manager`, so every `hasattr` guard stays on its local branch | `tui/app.py:447-454` |

### The trap this plan turns on

**Handles are brain state and the app does not treat them as such.**
`app.py:388` constructs a fresh `HandleRegistry()` unconditionally, so with
`bridge=` set there are **two** registries over one session set. Two views
minting at the same moment can mint the same handle, and the second pane to
mount would collide on `id=f"pane-{handle}"` inside its own app while the
manager believes one session exists.

This is invisible today only because nothing mints from two views at once.
It must be fixed in the same task that routes spawn through the manager —
routing spawn without sharing the registry moves the collision rather than
removing it.

### What this plan does NOT do

- **No transport.** Stage 5 still owns the socket and `aegis attach`.
- **No pane-level state sharing.** Scroll, drafts and focus stay per-view —
  that is stage 4's split and it stands. Only *tab identity* crosses.
- **No removal propagation beyond close.** A session closed on the brain
  removes its pane everywhere; reorder stays per-view for now (spec open
  question 4, still open).

---

### Task 1: `SessionManager` announces its session set

**Files:** Modify `src/aegis/core/manager.py`. Test:
`tests/views/test_session_observers.py` *(new)*.

**Interfaces:** `add_session_observer(cb: Callable[[str, AgentSession], None])`
where the first argument is `"added"` or `"removed"`. Fired after the session
list has been mutated, so an observer that reads `list_sessions()` sees the
new truth.

- [ ] **Step 1: Write the failing test**

Assert: an observer fires on spawn with `("added", session)`; fires on close
with `("removed", session)`; that **two** observers both fire (this is the
N-views property — a single-slot callback would pass a one-observer test and
fail the whole plan); and that an observer raising does not take the spawn
down with it, since one broken view must not stop the brain.

- [ ] **Step 2: Run it — expect `AttributeError: … has no attribute
      'add_session_observer'`**
- [ ] **Step 3: Implement.** List, not a slot. Fire after
      `self._sessions.append(s)` (`:249`) and after `self._sessions.remove(s)`
      (`:470`). Wrap each callback in `try/except Exception` and keep going.
- [ ] **Step 4: Run it — expect PASS**
- [ ] **Step 5: Mutation-check.** Make the observer store a single callback
      instead of a list → the two-observer test goes red. Remove the
      `try/except` → the broken-observer test goes red. Read both failures.
- [ ] **Step 6: Commit** — `feat(core): SessionManager announces added and
      removed sessions`

---

### Task 2: A bridged `AegisApp` mounts a pane per brain session

**Files:** Modify `src/aegis/tui/app.py`. Test:
`tests/views/test_session_propagation.py` *(new)*.

**Interfaces:** no new public surface. With `bridge` set, the app subscribes
on mount and mounts `ConversationPane(core=<AgentSession>, …)` for each
session that has no pane; on `"removed"` it unmounts.

**Why `core=`:** the pane must not build a second `AgentSession` over the same
harness. `core=` is the existing seam for precisely that (`pane.py:112`) and
an `AgentSession` satisfies its contract — both measured above.

- [ ] **Step 1: Write the failing test**

Assert, with two views over one manager: a session spawned on the manager
*after* both views are running reaches **both** pane sets; a session that
existed *before* a view attached is mounted at attach (the daemon case — a
view joining a running brain); both panes' `_core` **is** the manager's
session object, not a copy; and closing on the manager removes the pane from
both.

The identity assertion is the one that matters. Two panes over two *different*
`AgentSession`s wrapping one harness would satisfy a handle-equality check and
be a different, broken thing.

- [ ] **Step 2: Run it — expect the handles never to arrive**
- [ ] **Step 3: Implement.** Subscribe in the local-plane branch only
      (`bridge is not None`). Mount through `run_worker`, not bare
      `create_task` — the pane's `compose()` needs Textual's `active_app`
      ContextVar, which bare `create_task` does not propagate (the reason is
      already written at `app.py:2432-2437`).
- [ ] **Step 4: Run it — expect PASS**
- [ ] **Step 5: Mutation-check.** Return a copy instead of the live session
      → the identity assertion goes red. Skip the attach-time backfill →
      the pre-existing-session test goes red.
- [ ] **Step 6: Commit** — `feat(tui): a bridged app mounts a pane per brain
      session`

---

### Task 3: One spawn route, one handle registry

**Files:** Modify `src/aegis/tui/app.py` (`_SessionManagerAdapter.spawn`,
`AegisApp.__init__`). Test: extend
`tests/views/test_session_propagation.py`.

**Interfaces:** unchanged signatures. With a bridge, the adapter delegates to
`manager._sync_spawn(...)` and returns its `AgentSession`; the pane arrives
via Task 2's observer. `AegisApp` adopts `bridge.handles` instead of minting
its own registry.

- [ ] **Step 1: Enumerate before editing** — `grep -rn "_handles" src/ tests/`.
      Record the list; each is either a brain read to redirect or a local-path
      read to leave alone.
- [ ] **Step 2: Write the failing test.** `Ctrl+N` in view A (the real action,
      `action_new_tab`, not the adapter directly) puts the tab in view B; and
      the app and its bridge share **one** registry object, so a handle minted
      in either is unavailable in the other.
- [ ] **Step 3: Run it — expect the tab to stay in A**
- [ ] **Step 4: Implement.** Adopt `bridge.handles` where the bridge is
      adopted (beside `self.roots = bridge.roots`, `app.py:462-465`). Delegate
      the adapter's spawn. **The no-bridge branch keeps today's code
      unchanged** — `aegis serve --headless`, embedded hosts and the tests
      that build a bare `AegisApp` all run it.
- [ ] **Step 5: Run the FULL suite.** Not a blast radius: this changes the
      spawn route every interactive path takes, and the failures will be
      scattered. Read the rc directly, never through a pipe.
- [ ] **Step 6: Mutation-check.** Keep the app's own registry → the
      shared-registry test goes red for a stated reason.
- [ ] **Step 7: Commit** — `refactor(tui): one spawn route and one handle
      registry when bridged`

---

### Task 4: Flip the gate

**Files:** `tests/views/test_multi_view.py`, `TASKS.md`, the stage-4 plan's
status header.

- [ ] **Step 1: Delete the `xfail(strict=True)` marker and its reason.**
      Because it is `strict`, the suite has been failing on XPASS from the
      moment Task 3 landed — that is the marker doing its job.
- [ ] **Step 2: Run the gate — expect 5 passed, 0 xfailed**
- [ ] **Step 3: Mutation-check the gate itself.** Unsubscribe the observer in
      `app.py` → `test_a_session_opened_in_one_view_appears_in_the_other` goes
      red. A gate that cannot fail is worth less than none.
- [ ] **Step 4: Run the full suite** — expect no regression against 3629.
- [ ] **Step 5: Update the docs.** Stage-4 plan header stops saying a property
      is unbuilt; `TASKS.md` stage-5 entry stops leading with the xfail.
- [ ] **Step 6: Commit** — `test(views): the stage-4 gate is whole — tabs
      cross views`

---

## Done when

- `uv run pytest -q -m "not live"` is green with **no xfailed**, no regression
  against 3629.
- `tests/views/test_multi_view.py` passes all five and fails when the observer
  is unsubscribed.
- A handle minted in one view is unavailable in the other.
- `aegis`, `aegis serve`, `aegis web` and `--remote` are observably unchanged.

## Open questions this plan does not settle

1. **Tab order across views.** Order is brain state, so reordering in one view
   should reorder for everyone — not wired here. Spec open question 4.
2. **Focus on a session someone else opened.** A tab appearing in your view
   must not steal your focus; this plan mounts `foreground=False`, matching
   the queue-spawn path. Whether the *originating* view should focus it is a
   product question stage 5 can answer with a real client in hand.
