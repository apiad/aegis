# Aegis View Seam Implementation Plan (daemon stage 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run N independent `AegisApp` views over one brain in one process, each at its own geometry with its own focus, scroll and drafts, with view state persisted and restorable — exercised entirely in-process, with no transport.

**Architecture:** Textual's `WebDriver` already frames its output as `b"D" + length + ANSI` through an instance attribute (`_write`), so a subclass can redirect a view's output into a sink instead of stdout. One `AegisApp` is instantiated per view, holding the shared `SessionManager` by direct reference through the `bridge=` seam that landed in stage 3. `Workspace` splits into brain state (what exists, tab order) and per-view state (focus, scroll, drafts, geometry).

**Tech Stack:** Python 3.13, `uv`, pytest, Textual 8.2.6, typer.

**Spec:** `docs/superpowers/specs/2026-09-07-retire-web-ui-tui-over-web-design.md`

This plan covers **stage 4 only** of that spec's six. It carries no deletion: the web client, the WS plane and `--remote` are untouched and keep working. Stage 5 (transports, `aegis attach`, daemon lifecycle) and stage 6 (deletion) are separate plans.

## Scope decision: no transport in this stage

The spec's one-line sequencing says stage 4 is *"Exercised with `--foreground` and the unix socket only."* **This plan deliberately builds no socket.**

The seam's correctness — N views, independent geometry, the brain/view split, repaint on reattach — is fully observable in-process by reading the sink each view writes into. A test that reads a byte buffer fails for exactly one reason; a test mediated by IPC fails for two, and telling them apart costs a cycle every time. The unix socket is a transport and belongs with its sibling in stage 5, where `aegis attach` consumes it.

Consequence for stage 5's plan: it inherits a `View` object with a sink and a feed method, and its work is carrying those bytes over a socket rather than inventing the seam.

## Global Constraints

- **Python ≥ 3.13**, `uv` only — `uv run pytest`, never bare `pip`/`python`.
- **English** for all code, comments, identifiers, test names and commit messages.
- **Conventional commits**; commit after every task.
- **No behaviour change for the single-view path.** `aegis`, `aegis serve`, `aegis web` and `--remote` must be observably identical after every task in this plan. There is no sanctioned exception, unlike stages 1–3.
- **Never `git add -A`.** Stage the explicit paths each task names. This is a shared checkout.
- **Baseline is 3592 passed, 1 skipped, rc=0** on `uv run pytest -q -m "not live"`. Treat any red as a regression, not noise — the inotify flakes described in older docs no longer occur.
- Two known failures, unrelated, **do not chase**: `tests/test_drivers_multiprovider_live.py` (the real `gemini` CLI is broken on this box; `live`-marked, excluded by `-m "not live"`), and `tests/test_dsl_mcp.py::test_run_dynamic_workflow_autoapprove_and_launch` (fails only when run after `test_workflow_mcp.py` due to a global workflow-registry reset; passes alone).
- **`asyncio_mode = "auto"`** (`pyproject.toml:92`) — async tests need no `@pytest.mark.asyncio`.

## Facts measured against the installed tree on 2026-09-11

Every one of these was verified by reading the installed source, not inferred. Line numbers are Textual **8.2.6** and `aegis` at `9c903ca`.

| Fact | Where |
|---|---|
| `WebDriver._write = partial(os.write, self.fileno)` — an **instance** attribute, so redirectable per instance | `textual/drivers/web_driver.py:63` |
| Data frames are `b"D" + len.to_bytes(4, "big") + utf-8 ANSI` | `web_driver.py:87` |
| There are **three** frame types, not one: `b"D"` data, `b"M"` meta, `b"P"` binary-encoded | `web_driver.py:87`, `:97`, `:106` |
| `start_application_mode` writes a `b"__GANGLION__\n"` handshake line | `web_driver.py:154` |
| `start_application_mode` installs **its own** SIGINT/SIGTERM handlers via `loop.add_signal_handler` | `web_driver.py:150-152` |
| `WebDriver.__init__(app, *, debug, mouse, size)`; `size` falls back to the **`COLUMNS`/`ROWS` env vars** | `web_driver.py:44-59` |
| `self.stdout` / `self.fileno` are bound to `sys.__stdout__` in `__init__` | `web_driver.py:61-62` |
| `run_input_thread` reads real stdin through `InputReader` | `web_driver.py:68`, `:184` |
| `App.__init__` accepts `driver_class` | `textual/app.py:574`, stored at `:637` |
| The driver is constructed as `driver_class(self, debug=…, mouse=…, size=size)` — **positional app, keyword rest** | `textual/app.py:3345-3350` |
| `render_update` returns a partial update unless the whole screen region is dirty | `textual/_compositor.py:1118` |
| `_dirty_regions` is the set to add to for a forced full frame | `textual/_compositor.py:309`, `:1275` |
| **`App.run_test()` defaults to `headless=True`, which replaces `self.driver_class` with `HeadlessDriver`** | `textual/app.py:3334-3337`; default at `App.run_test` signature |
| `Screen._compositor` is an **instance** attribute, set in `__init__` — not visible via `dir(Screen)` | `textual/screen.py:291` |
| `Driver.__init__` stores `self._size` | `textual/driver.py` |
| `AegisApp.__init__` calls `super().__init__()` with **no arguments** | `src/aegis/tui/app.py:322` |
| `AegisApp(bridge=…)` injects a local manager and adopts its roots | `src/aegis/tui/app.py:423-437` |
| `Workspace` holds `active_handle` beside tab identity and `order` | `src/aegis/state/workspace.py:60-65` |
| Existing state subdirs: `canvases`, `hooks`, `queues`, `schedules`, `sessions`, `ssh`, `terminals`, `tools` — `views` is new | `grep 'state_dir /' src/aegis/` |

### The four traps this stage turns on

The spec says the driver work is a subclass that overrides `_write`. That is true and insufficient. `WebDriver` was written for exactly one view attached to one process's stdout, and three other things in it are process-global:

1. **Signals.** `start_application_mode` adds SIGINT/SIGTERM handlers to the running loop. N views means N registrations on one loop, last-registration-wins, and a daemon whose views quietly own the host's shutdown. Must not run.
2. **Stdin.** `run_input_thread` reads the process's real stdin through `InputReader`. N views cannot each own stdin, and a daemon has none. Must not run.
3. **Geometry via env.** `size=None` falls back to `COLUMNS`/`ROWS`, which are process-global. Per-view geometry must be passed as an explicit `size=`, never arranged by setting env.
4. **The handshake line.** `b"__GANGLION__\n"` is written before the first frame for the benefit of Textual's own web server. Nothing in aegis consumes it, and a stage-5 attach client reading raw frames would see it as a malformed frame.

### A fifth trap, in the tests rather than the code

**`App.run_test()` runs headless by default, and headless swaps the driver.** `app.py:3334-3337` reads `if headless: driver_class = HeadlessDriver` — it ignores `self.driver_class` entirely. So any test that drives a view through the default `run_test()` exercises `HeadlessDriver`, and the view's sink stays empty.

That is not a loud failure. `assert view.frames` fails for a reason that looks like a broken sink, and — far worse — **`assert not other.frames` passes vacuously**: empty because no `ViewDriver` ever ran, not because frames did not cross. That assertion is the whole point of the seam, so a vacuous pass here would certify nothing while looking green.

Every test in this plan that drives a view therefore passes `headless=False`, and asserts that the live driver really is a `ViewDriver` before believing anything about the frames. `headless=False` is safe precisely *because* `ViewDriver` exists: it takes no stdin, installs no signal handlers, and writes to a sink rather than a terminal — which is exactly what makes a non-headless app safe to run inside a test process.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/aegis/views/__init__.py` *(new)* | Package marker; re-exports `View`, `ViewRegistry`. |
| `src/aegis/views/driver.py` *(new)* | `ViewDriver` + `view_driver_for(sink)`. The four overrides above. Nothing else. |
| `src/aegis/views/view.py` *(new)* | `View` — one `AegisApp` bound to one sink and one geometry, plus `feed()` and `repaint()`. |
| `src/aegis/views/registry.py` *(new)* | `ViewRegistry` — N views over one brain, keyed by view id; open/close/get/list. |
| `src/aegis/views/state.py` *(new)* | `ViewState` dataclass + `load_view` / `save_view` under `.aegis/state/views/<id>.json`. |
| `src/aegis/state/workspace.py` | Loses `active_handle` — it is view state, not brain state. |
| `src/aegis/tui/app.py` | `driver_class=` pass-through to `App.__init__`; `active_handle` read from the view, not the workspace. |
| `tests/views/*` *(new)* | Per-task tests plus the stage gate in `tests/views/test_multi_view.py`. |

---

### Task 1: `ViewDriver` — a driver whose output is a sink

**Files:**
- Create: `src/aegis/views/__init__.py`, `src/aegis/views/driver.py`
- Test: `tests/views/test_view_driver.py`

**Interfaces:**
- Consumes: nothing from this plan.
- Produces: `view_driver_for(sink: Callable[[bytes], None]) -> type[ViewDriver]` — returns a `WebDriver` subclass bound to that sink. `ViewDriver` keeps `WebDriver`'s constructor signature exactly: `(app, *, debug=False, mouse=True, size=None)`, because `App` calls it positionally-then-keyword at `app.py:3345`.

**Why a class factory and not a partial:** `App` stores a driver *class* (`app.py:637`) and instantiates it itself. There is no seam to pass a sink through, so the sink is bound onto a generated subclass.

- [ ] **Step 1: Write the failing test**

```python
# tests/views/test_view_driver.py
"""ViewDriver is WebDriver with every process-global assumption removed.

WebDriver was written for one view attached to one process's stdout. Four
things in it are process-global and each breaks N-views-in-one-process:
its output fd, its SIGINT/SIGTERM handlers, its stdin reader, and the
COLUMNS/ROWS fallback for geometry.
"""
import os
import signal

import pytest

from aegis.views.driver import ViewDriver, view_driver_for


class _FakeApp:
    """Enough App for a Driver to construct. Driver.__init__ stores the app
    and reads nothing off it."""


def test_factory_returns_a_webdriver_subclass_bound_to_the_sink():
    frames = []
    cls = view_driver_for(frames.append)
    assert issubclass(cls, ViewDriver)
    drv = cls(_FakeApp(), size=(80, 24))
    drv.write("hello")
    assert frames, "nothing reached the sink"
    assert frames[0].startswith(b"D"), frames[0]


def test_frame_is_the_textual_wire_format():
    """b'D' + 4-byte big-endian length + utf-8 payload (web_driver.py:87)."""
    frames = []
    drv = view_driver_for(frames.append)(_FakeApp(), size=(80, 24))
    drv.write("hi")
    body = "hi".encode("utf-8")
    assert frames[0] == b"D" + len(body).to_bytes(4, "big") + body


def test_two_drivers_have_independent_sinks_and_sizes():
    """The whole point. COLUMNS/ROWS is process-global (web_driver.py:52-59),
    so geometry must ride on the explicit size= argument."""
    a, b = [], []
    da = view_driver_for(a.append)(_FakeApp(), size=(80, 24))
    db = view_driver_for(b.append)(_FakeApp(), size=(140, 50))
    da.write("A")
    assert a and not b, "sinks are not independent"
    assert da._size == (80, 24)
    assert db._size == (140, 50)


def test_nothing_reaches_the_real_stdout(capfdbinary):
    drv = view_driver_for(lambda _b: None)(_FakeApp(), size=(80, 24))
    drv.write("should not appear")
    drv.flush()
    out, err = capfdbinary.readouterr()
    assert out == b"", out
    assert b"should not appear" not in err


def test_start_application_mode_installs_no_signal_handlers():
    """web_driver.py:150-152 adds SIGINT/SIGTERM handlers to the running
    loop. N views on one loop means last-registration-wins, and a daemon
    whose views own the host's shutdown."""
    before = signal.getsignal(signal.SIGINT)
    frames = []
    drv = view_driver_for(frames.append)(_FakeApp(), size=(80, 24))
    drv.start_application_mode()
    assert signal.getsignal(signal.SIGINT) is before


def test_start_application_mode_writes_no_ganglion_handshake():
    """web_driver.py:154. Nothing in aegis consumes it and a stage-5 attach
    client reading raw frames would see it as a malformed frame."""
    frames = []
    drv = view_driver_for(frames.append)(_FakeApp(), size=(80, 24))
    drv.start_application_mode()
    assert not any(b"__GANGLION__" in f for f in frames)


def test_input_thread_is_never_started():
    """run_input_thread reads the process's real stdin (web_driver.py:184).
    N views cannot each own stdin, and a daemon has none."""
    drv = view_driver_for(lambda _b: None)(_FakeApp(), size=(80, 24))
    drv.start_application_mode()
    assert not drv._key_thread.is_alive()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/views/test_view_driver.py -v -m "not live"`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.views'`

- [ ] **Step 3: Write the implementation**

```python
# src/aegis/views/driver.py
"""A Textual driver whose output is a byte sink rather than a terminal.

``WebDriver`` already frames its output as ``b"D" + length + ANSI`` through
``self._write``, which is an *instance* attribute (`web_driver.py:63`) and so
can be redirected per instance. That is what makes many views in one process
possible at all.

It is not sufficient on its own. ``WebDriver`` was written for exactly one
view attached to one process's stdout, and three other things in it are
process-global: it installs SIGINT/SIGTERM handlers on the running loop
(`:150-152`), it reads the process's real stdin (`:184`), and with
``size=None`` it takes its geometry from the ``COLUMNS``/``ROWS`` env vars
(`:52-59`). Each of those is fine for one view and wrong for N. It also
writes a ``__GANGLION__`` handshake line (`:154`) that nothing in aegis
consumes.
"""
from __future__ import annotations

from typing import Callable

from textual.drivers.web_driver import WebDriver


class ViewDriver(WebDriver):
    """One view's output, framed and handed to ``_sink``.

    Subclasses generated by :func:`view_driver_for` bind ``_sink``. The
    constructor signature is ``WebDriver``'s unchanged, because ``App``
    instantiates the class itself as
    ``driver_class(app, debug=…, mouse=…, size=…)`` (`app.py:3345-3350`)
    and there is no seam to pass anything else through.
    """

    #: Bound by ``view_driver_for``. A plain attribute rather than a
    #: constructor argument for the reason above.
    _sink: Callable[[bytes], None] = staticmethod(lambda _b: None)

    def __init__(self, app, *, debug: bool = False, mouse: bool = True,
                 size: tuple[int, int] | None = None) -> None:
        super().__init__(app, debug=debug, mouse=mouse, size=size)
        # AFTER super(), which binds _write to sys.__stdout__ (`:61-63`).
        self._write = self._emit

    def _emit(self, data: bytes) -> None:
        type(self)._sink(data)

    def start_application_mode(self) -> None:
        """Enter application mode without owning the process.

        Deliberately does NOT call ``super()``: that installs signal
        handlers and writes the ganglion handshake. The escape sequences
        below are the rest of what it does (`web_driver.py:156-160`), and
        they are per-view state that belongs in the frame stream.
        """
        self.write("\x1b[?1049h")   # alt screen
        self._enable_mouse_support()
        self.write("\x1b[?25l")     # hide cursor
        self.write("\033[?1003h")   # mouse movement reporting

    def run_input_thread(self) -> None:
        """No stdin. Input arrives through the transport, not the tty."""
        return

    def disable_input(self) -> None:
        return


def view_driver_for(sink: Callable[[bytes], None]) -> type[ViewDriver]:
    """A ``ViewDriver`` subclass whose frames go to ``sink``."""
    return type("BoundViewDriver", (ViewDriver,),
                {"_sink": staticmethod(sink)})
```

```python
# src/aegis/views/__init__.py
from aegis.views.driver import ViewDriver, view_driver_for

__all__ = ["ViewDriver", "view_driver_for"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/views/test_view_driver.py -v -m "not live"`
Expected: PASS (7 tests)

- [ ] **Step 5: Mutation-check the three overrides**

Each override exists because of a specific process-global. A test that
cannot see the override removed is not testing it. For each of the three,
delete the override, confirm the named test goes RED, restore:

```bash
# 1. delete start_application_mode  -> signal + ganglion tests go red
# 2. delete run_input_thread        -> input-thread test goes red
# 3. move `self._write = self._emit` above super().__init__()
#    -> sink tests go red (super() rebinds _write to stdout)
```

Expected: three reds, each naming its own test. Record which test caught which.
The third is the subtle one: ordering, not presence.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/views/__init__.py src/aegis/views/driver.py tests/views/test_view_driver.py
git commit -m "feat(views): ViewDriver — a Textual driver that writes to a sink

WebDriver frames output through an instance _write, so a subclass can
redirect it per instance. Three other things in it are process-global and
break N views in one process: loop signal handlers, the stdin reader, and
the COLUMNS/ROWS geometry fallback. All three are overridden, and the
ganglion handshake nothing in aegis consumes is dropped."
```

---

### Task 2: `AegisApp(driver_class=…)` — let a view choose its driver

**Files:**
- Modify: `src/aegis/tui/app.py:317-322` (signature and the bare `super().__init__()`)
- Test: `tests/views/test_app_driver_injection.py`

**Interfaces:**
- Consumes: `view_driver_for` from Task 1.
- Produces: `AegisApp(..., driver_class: type | None = None)` — forwarded to `App.__init__`. `None` keeps today's auto-detection exactly.

- [ ] **Step 1: Write the failing test**

```python
# tests/views/test_app_driver_injection.py
"""AegisApp must be able to run on a ViewDriver. It calls
super().__init__() with no arguments today (app.py:322), so driver_class
never reaches Textual."""
from aegis.tui.app import AegisApp
from aegis.views.driver import view_driver_for


def _app(**kw):
    return AegisApp(agents={}, default_agent="", make_session=None,
                    mcp=None, **kw)


def test_driver_class_reaches_textual():
    frames = []
    cls = view_driver_for(frames.append)
    app = _app(driver_class=cls)
    assert app.driver_class is cls


def test_no_driver_class_keeps_auto_detection():
    """The single-view path must be observably identical."""
    app = _app()
    assert app.driver_class is not None
    assert not issubclass(app.driver_class,
                          __import__("aegis.views.driver",
                                     fromlist=["ViewDriver"]).ViewDriver)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/views/test_app_driver_injection.py -v -m "not live"`
Expected: FAIL — `AegisApp.__init__() got an unexpected keyword argument 'driver_class'`

- [ ] **Step 3: Add the parameter and forward it**

In `src/aegis/tui/app.py`, add to the keyword-only block ending at `:321`:

```python
                 bridge: "object | None" = None,
                 driver_class: "type | None" = None) -> None:
```

and replace the bare `super().__init__()` at `:322`:

```python
        # A view supplies its own driver so its frames go to that view's
        # sink instead of this process's stdout. None keeps Textual's
        # auto-detection, which is every existing caller.
        super().__init__(driver_class=driver_class)
```

- [ ] **Step 4: Run the tests and the blast radius**

Run: `uv run python -m pytest tests/views/test_app_driver_injection.py -v -m "not live"`
Expected: PASS (2 tests)

Run: `uv run python -m pytest tests/ -k "app or tui or pane or remote" -q -m "not live"`
Expected: PASS. `super().__init__()` took no arguments before, so a failure
here means `App.__init__` rejects something — read the error, do not add a guard.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/tui/app.py tests/views/test_app_driver_injection.py
git commit -m "feat(tui): AegisApp(driver_class=) forwards to Textual"
```

---

### Task 3: `ViewState` — what belongs to a view rather than the brain

**Files:**
- Create: `src/aegis/views/state.py`
- Test: `tests/views/test_view_state.py`

**Interfaces:**
- Consumes: `AegisRoots` (stage 1), for `roots.state_dir`.
- Produces: `ViewState(view_id: str, geometry: tuple[int, int], active_handle: str | None, scroll: dict[str, int], drafts: dict[str, str])`; `save_view(state_dir: Path, vs: ViewState) -> None`; `load_view(state_dir: Path, view_id: str) -> ViewState | None`. Files live at `<state_dir>/views/<view_id>.json`.

**The split, from the spec.** Brain: the session set, tab **order**, transcripts, agent state, metrics, plans, titles, queues, monitors, canvas, terminals, hosts, and **pending messages**. View: focused tab, geometry, scroll per tab, draft text per tab, unseen markers, open modals.

**The line that is easy to get wrong and is worth reading twice:** a *draft* is per-view; a *pending message* is not. `PendingStrip` holds messages already submitted while the agent is mid-turn, queued for the turn boundary and cancellable by clicking a chip. Per-view they would show different queues for one agent, and cancelling in one view would not cancel in the other. **Text still in the box is yours; text you have sent is everyone's.** Task 7 verifies this against the code rather than assuming it.

- [ ] **Step 1: Write the failing test**

```python
# tests/views/test_view_state.py
from aegis.views.state import ViewState, load_view, save_view


def test_roundtrip(tmp_path):
    vs = ViewState(view_id="tty-1", geometry=(140, 50),
                   active_handle="lucid-knuth",
                   scroll={"lucid-knuth": 42}, drafts={"lucid-knuth": "half "})
    save_view(tmp_path, vs)
    assert load_view(tmp_path, "tty-1") == vs


def test_missing_view_is_none_not_an_error(tmp_path):
    """A first attach has no prior state; that is the normal case, not a
    failure."""
    assert load_view(tmp_path, "never-seen") is None


def test_two_views_do_not_share_a_file(tmp_path):
    save_view(tmp_path, ViewState("a", (80, 24), None, {}, {"h": "draft-a"}))
    save_view(tmp_path, ViewState("b", (140, 50), None, {}, {"h": "draft-b"}))
    assert load_view(tmp_path, "a").drafts == {"h": "draft-a"}
    assert load_view(tmp_path, "b").drafts == {"h": "draft-b"}


def test_lands_under_the_views_subdir(tmp_path):
    save_view(tmp_path, ViewState("tty-1", (80, 24), None, {}, {}))
    assert (tmp_path / "views" / "tty-1.json").is_file()


def test_a_damaged_view_file_is_none_not_a_raise(tmp_path):
    """A corrupt view file must cost that view its scroll position, never
    the daemon. Same posture as state/session_log.py's scan_log."""
    (tmp_path / "views").mkdir()
    (tmp_path / "views" / "tty-1.json").write_text("{not json", encoding="utf-8")
    assert load_view(tmp_path, "tty-1") is None


def test_a_view_id_cannot_escape_the_views_dir(tmp_path):
    """View ids arrive from clients in stage 5. A path separator in one must
    not write outside the state dir."""
    import pytest
    with pytest.raises(ValueError):
        save_view(tmp_path, ViewState("../../etc/passwd", (80, 24), None, {}, {}))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/views/test_view_state.py -v -m "not live"`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.views.state'`

- [ ] **Step 3: Write the implementation**

```python
# src/aegis/views/state.py
"""Per-view state: what you are looking at, not what exists.

Opening a tab opens it for everyone; what you have focused, scrolled to and
half-typed is yours. ``state/workspace.py`` holds the brain half — which
tabs exist and in what order.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ViewState:
    view_id: str
    geometry: tuple[int, int]
    active_handle: str | None = None
    scroll: dict[str, int] = field(default_factory=dict)
    drafts: dict[str, str] = field(default_factory=dict)


def _path(state_dir: Path, view_id: str) -> Path:
    # View ids come from clients in stage 5. Reject anything that is not a
    # single path component rather than sanitising it: a silently rewritten
    # id resolves to a different view than the client believes it has.
    if view_id != Path(view_id).name or view_id in ("", ".", ".."):
        raise ValueError(f"invalid view id: {view_id!r}")
    return state_dir / "views" / f"{view_id}.json"


def save_view(state_dir: Path, vs: ViewState) -> None:
    p = _path(state_dir, vs.view_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "view_id": vs.view_id,
        "geometry": list(vs.geometry),
        "active_handle": vs.active_handle,
        "scroll": vs.scroll,
        "drafts": vs.drafts,
    }
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(p)          # atomic; a torn view file costs a scroll position


def load_view(state_dir: Path, view_id: str) -> ViewState | None:
    p = _path(state_dir, view_id)
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return ViewState(
            view_id=raw["view_id"],
            geometry=tuple(raw["geometry"]),
            active_handle=raw.get("active_handle"),
            scroll=raw.get("scroll", {}),
            drafts=raw.get("drafts", {}),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        # A damaged view file must never take the daemon down.
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/views/test_view_state.py -v -m "not live"`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/aegis/views/state.py tests/views/test_view_state.py
git commit -m "feat(views): ViewState — focus, scroll, drafts and geometry per view"
```

---

### Task 4: Move `active_handle` off the brain

**Files:**
- Modify: `src/aegis/state/workspace.py:60-65` (`Workspace`), `:75-98` (`save`), `:100-152` (`load`)
- Modify: every `Workspace(...)` construction and `active_handle` read — enumerate with the command in Step 1
- Test: `tests/views/test_workspace_is_brain_only.py`

**Interfaces:**
- Consumes: `ViewState` from Task 3.
- Produces: `Workspace` without `active_handle`. Callers that need it read `ViewState.active_handle`.

**Why this is its own task:** `Workspace` is the one place the spec names as conflating the two halves — it stores `active_handle` (pure view state) beside tab identity and `order` (brain state). Leaving it means two sources of truth for focus the moment a second view exists.

- [ ] **Step 1: Enumerate the call sites before editing**

Run:
```bash
grep -rn "active_handle" src/ tests/
```
Record the list in your final report. Every one is either a brain read to delete or a view read to redirect.

- [ ] **Step 2: Write the failing test**

```python
# tests/views/test_workspace_is_brain_only.py
"""Workspace is what EXISTS. Focus is what a given view is looking at, and
two views focus different tabs, so it cannot live here."""
import dataclasses

from aegis.state.workspace import Workspace


def test_workspace_has_no_active_handle():
    fields = {f.name for f in dataclasses.fields(Workspace)}
    assert "active_handle" not in fields, (
        "active_handle is view state; it belongs in ViewState. Keeping it "
        "here gives focus two sources of truth as soon as a second view "
        "exists")


def test_saved_workspace_carries_no_focus(tmp_path):
    from aegis.state.workspace import load, save
    ws = load(tmp_path) or Workspace(tabs=[], terminals=[], files=[])
    save(tmp_path, ws)
    import json
    raw = json.loads((tmp_path / "workspace.json").read_text(encoding="utf-8"))
    assert "active_handle" not in raw
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run python -m pytest tests/views/test_workspace_is_brain_only.py -v -m "not live"`
Expected: FAIL — `active_handle` is still a field.

- [ ] **Step 4: Remove the field**

Delete `active_handle` from the `Workspace` dataclass (`:61`), from the dict
`save` builds (`:80`), and from the `Workspace(...)` that `load` returns
(`:150`). `load` must tolerate an **old** file that still has the key —
it is simply ignored, so an existing state dir keeps working.

Redirect each reader found in Step 1. In `AegisApp`, the active tab is now
the view's concern; until Task 5 gives the app a view, read and write it on
an instance attribute initialised to `None`.

- [ ] **Step 5: Run the tests and the blast radius**

Run: `uv run python -m pytest tests/views/test_workspace_is_brain_only.py -v -m "not live"`
Expected: PASS (2 tests)

Run: `uv run python -m pytest tests/ -k "workspace or resume or app or history or doctor" -q -m "not live"`
Expected: PASS.

- [ ] **Step 6: Verify an old state file still loads**

```bash
uv run python - <<'PY'
import json, pathlib, tempfile
from aegis.state.workspace import load
d = pathlib.Path(tempfile.mkdtemp())
(d / "workspace.json").write_text(json.dumps({
    "active_handle": "lucid-knuth", "tabs": [], "terminals": [], "files": [],
    "saved_at": "2026-01-01T00:00:00Z"}), encoding="utf-8")
ws = load(d)
print("loaded:", ws is not None, "| has focus:", hasattr(ws, "active_handle"))
PY
```
Expected: `loaded: True | has focus: False`

- [ ] **Step 7: Commit**

```bash
git add src/aegis/state/workspace.py src/aegis/tui/app.py tests/views/test_workspace_is_brain_only.py
git commit -m "refactor(state): Workspace is brain state; focus moves to ViewState

Workspace stored active_handle beside tab identity and order. Focus is
per-view -- two views look at different tabs -- so keeping it there gives
it two sources of truth the moment a second view exists. Old workspace
files still load; the key is ignored."
```

---

### Task 5: `View` — one app, one sink, one geometry

**Files:**
- Create: `src/aegis/views/view.py`
- Test: `tests/views/test_view.py`

**Interfaces:**
- Consumes: `view_driver_for` (Task 1), `AegisApp(driver_class=…)` (Task 2), `ViewState` (Task 3).
- Produces:
  - `View(view_id: str, app: AegisApp, state: ViewState)` with `frames: list[bytes]` (the sink's buffer), `async def run() -> None`, `async def stop() -> None`, `def repaint() -> None`.
  - `async def open_view(view_id, *, manager, geometry, roots, **app_kw) -> View` — builds the app on a bound driver, restores persisted view state if any.

- [ ] **Step 1: Write the failing test**

```python
# tests/views/test_view.py
"""A View is one AegisApp bound to one sink at one geometry. The app holds
the brain by direct reference through bridge= (stage 3), so there is no
protocol between the view and the manager."""
from pathlib import Path

from aegis.config.roots import AegisRoots
from aegis.core.manager import SessionManager
from aegis.views.state import ViewState, save_view
from aegis.views.view import open_view


class _FakeHarness:
    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        if False:
            yield


def _mgr(roots):
    return SessionManager({"default": object()}, "default",
                          make_session=lambda p, u, h: _FakeHarness(),
                          mcp=None, roots=roots)


async def test_view_holds_the_manager_by_reference(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    mgr = _mgr(roots)
    v = await open_view("tty-1", manager=mgr, geometry=(80, 24), roots=roots)
    assert v.app.manager is mgr
    await v.stop()


async def test_view_restores_persisted_state(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    save_view(roots.state_dir, ViewState("tty-1", (140, 50), "lucid-knuth",
                                         {"lucid-knuth": 12},
                                         {"lucid-knuth": "half typed"}))
    v = await open_view("tty-1", manager=_mgr(roots), geometry=(140, 50),
                        roots=roots)
    assert v.state.drafts == {"lucid-knuth": "half typed"}
    assert v.state.active_handle == "lucid-knuth"
    await v.stop()


async def test_a_first_attach_has_no_prior_state(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    v = await open_view("brand-new", manager=_mgr(roots), geometry=(80, 24),
                        roots=roots)
    assert v.state.drafts == {}
    assert v.state.geometry == (80, 24)
    await v.stop()


async def test_geometry_is_the_drivers_and_not_the_environments(tmp_path,
                                                                monkeypatch):
    """COLUMNS/ROWS is process-global (web_driver.py:52-59). A view's size
    must come from its own argument or N views share one geometry."""
    monkeypatch.setenv("COLUMNS", "999")
    monkeypatch.setenv("ROWS", "999")
    roots = AegisRoots.for_project(tmp_path)
    v = await open_view("tty-1", manager=_mgr(roots), geometry=(80, 24),
                        roots=roots)
    assert v.state.geometry == (80, 24)
    await v.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/views/test_view.py -v -m "not live"`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.views.view'`

- [ ] **Step 3: Write the implementation**

```python
# src/aegis/views/view.py
"""One view: an AegisApp whose frames go to a sink, at its own geometry.

The app holds the brain by direct Python reference through ``bridge=``
(stage 3), so nothing is serialised between a view and the manager and
there is no protocol that can fall behind. What crosses a view's boundary
is bytes out and key events in — nothing that knows what a session is.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from aegis.config.roots import AegisRoots
from aegis.tui.app import AegisApp
from aegis.views.driver import view_driver_for
from aegis.views.state import ViewState, load_view, save_view


@dataclass
class View:
    view_id: str
    app: AegisApp
    state: ViewState
    frames: list[bytes] = field(default_factory=list)
    _task: asyncio.Task | None = None

    async def run(self) -> None:
        """Run the app until it exits. The caller owns the task."""
        self._task = asyncio.create_task(self.app.run_async())

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self.app.exit()
            with_timeout = asyncio.wait_for(self._task, timeout=10)
            try:
                await with_timeout
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
        self._task = None

    def repaint(self) -> None:
        """Force the next render cycle to emit a full frame.

        Textual emits *incremental* updates: ``render_update`` returns a
        partial unless the whole screen region is dirty
        (`_compositor.py:1118`), and there is no repaint meta message. So a
        reattaching client would otherwise receive deltas against a screen
        it has never seen. Measured: ``app.refresh(repaint=True,
        layout=True)`` emits nothing; dirtying the screen region does.
        """
        screen = self.app.screen
        compositor = screen._compositor
        compositor._dirty_regions.add(screen.size.region)
        screen.refresh()

    def persist(self, state_dir: Path) -> None:
        save_view(state_dir, self.state)


async def open_view(view_id: str, *, manager, geometry: tuple[int, int],
                    roots: AegisRoots, **app_kw) -> View:
    """Build a view, restoring its persisted state if it has any."""
    frames: list[bytes] = []
    restored = load_view(roots.state_dir, view_id)
    state = restored or ViewState(view_id=view_id, geometry=geometry)
    # Geometry always comes from this attach, not from the last one: the
    # terminal may have been resized between them.
    state.geometry = geometry

    app = AegisApp(
        agents=app_kw.pop("agents", {}),
        default_agent=app_kw.pop("default_agent", ""),
        make_session=app_kw.pop("make_session", None),
        mcp=app_kw.pop("mcp", None),
        bridge=manager,
        driver_class=view_driver_for(frames.append),
        **app_kw)
    return View(view_id=view_id, app=app, state=state, frames=frames)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/views/test_view.py -v -m "not live"`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/aegis/views/view.py tests/views/test_view.py
git commit -m "feat(views): View — one AegisApp bound to one sink and geometry"
```

---

### Task 6: `ViewRegistry` — N views over one brain

**Files:**
- Create: `src/aegis/views/registry.py`
- Modify: `src/aegis/views/__init__.py` (re-export)
- Test: `tests/views/test_view_registry.py`

**Interfaces:**
- Consumes: `View`, `open_view` (Task 5).
- Produces: `ViewRegistry(manager, roots, **app_kw)` with `async def open(view_id, geometry) -> View`, `async def close(view_id) -> None`, `def get(view_id) -> View | None`, `def list() -> list[str]`, `async def close_all() -> None`. Re-opening a live `view_id` returns the existing view rather than a second one.

- [ ] **Step 1: Write the failing test**

```python
# tests/views/test_view_registry.py
from aegis.config.roots import AegisRoots
from aegis.core.manager import SessionManager
from aegis.views.registry import ViewRegistry


class _FakeHarness:
    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        if False:
            yield


def _reg(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    mgr = SessionManager({"default": object()}, "default",
                         make_session=lambda p, u, h: _FakeHarness(),
                         mcp=None, roots=roots)
    return ViewRegistry(manager=mgr, roots=roots), mgr


async def test_two_views_are_two_apps_over_one_manager(tmp_path):
    reg, mgr = _reg(tmp_path)
    a = await reg.open("tty-1", (80, 24))
    b = await reg.open("web-1", (140, 50))
    assert a.app is not b.app
    assert a.app.manager is mgr and b.app.manager is mgr
    await reg.close_all()


async def test_reopening_a_live_id_returns_the_same_view(tmp_path):
    """A reconnect must not build a second app for one client."""
    reg, _ = _reg(tmp_path)
    a = await reg.open("tty-1", (80, 24))
    again = await reg.open("tty-1", (80, 24))
    assert again is a
    await reg.close_all()


async def test_closing_one_view_leaves_the_other(tmp_path):
    reg, _ = _reg(tmp_path)
    await reg.open("tty-1", (80, 24))
    await reg.open("web-1", (140, 50))
    await reg.close("tty-1")
    assert reg.list() == ["web-1"]
    await reg.close_all()


async def test_closing_a_view_persists_its_state(tmp_path):
    """A view outlives its socket. Reattaching restores focus and drafts."""
    from aegis.views.state import load_view
    reg, _ = _reg(tmp_path)
    v = await reg.open("tty-1", (80, 24))
    v.state.drafts["lucid-knuth"] = "half typed"
    await reg.close("tty-1")
    roots = AegisRoots.for_project(tmp_path)
    assert load_view(roots.state_dir, "tty-1").drafts == {
        "lucid-knuth": "half typed"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/views/test_view_registry.py -v -m "not live"`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.views.registry'`

- [ ] **Step 3: Write the implementation**

```python
# src/aegis/views/registry.py
"""Every view attached to one brain.

A view outlives its socket: closing one persists its state under its id, so
a reattach restores focus, scroll and drafts. Keyed by client id --
localStorage for browsers, per-tty for terminals -- which is why re-opening
a live id returns the existing view rather than building a second app for
one client.
"""
from __future__ import annotations

from aegis.config.roots import AegisRoots
from aegis.views.view import View, open_view


class ViewRegistry:
    def __init__(self, *, manager, roots: AegisRoots, **app_kw) -> None:
        self._manager = manager
        self._roots = roots
        self._app_kw = app_kw
        self._views: dict[str, View] = {}

    async def open(self, view_id: str, geometry: tuple[int, int]) -> View:
        existing = self._views.get(view_id)
        if existing is not None:
            return existing
        v = await open_view(view_id, manager=self._manager,
                            geometry=geometry, roots=self._roots,
                            **self._app_kw)
        self._views[view_id] = v
        return v

    def get(self, view_id: str) -> View | None:
        return self._views.get(view_id)

    def list(self) -> list[str]:
        return list(self._views)

    async def close(self, view_id: str) -> None:
        v = self._views.pop(view_id, None)
        if v is None:
            return
        v.persist(self._roots.state_dir)
        await v.stop()

    async def close_all(self) -> None:
        for view_id in list(self._views):
            await self.close(view_id)
```

Add to `src/aegis/views/__init__.py`:

```python
from aegis.views.registry import ViewRegistry
from aegis.views.state import ViewState
from aegis.views.view import View, open_view

__all__ = ["ViewDriver", "view_driver_for", "View", "open_view",
           "ViewRegistry", "ViewState"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/views/test_view_registry.py -v -m "not live"`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/aegis/views/registry.py src/aegis/views/__init__.py tests/views/test_view_registry.py
git commit -m "feat(views): ViewRegistry — N views over one brain, persisted on close"
```

---

### Task 7: Pending messages are brain state — verify, then fix if not

**Files:**
- Read: `src/aegis/tui/pending.py`, `src/aegis/core/session.py` (`cancel_pending`, the buffered-message path)
- Test: `tests/views/test_pending_is_brain_state.py`

**Interfaces:**
- Consumes: `ViewRegistry` (Task 6).
- Produces: no new API if the code is already correct. **This task may legitimately produce only a test.**

**Why this task exists.** The spec calls this out as the one line that is easy to get wrong: a *draft* is per-view, a *pending message* is not. Pending messages are submitted and awaiting a turn boundary; per-view they would show different queues for one agent, and cancelling in one view would not cancel in the other. The buffer lives on `AgentSession` (brain) while `PendingStrip` is a widget (view), so this is **probably already right** — but "probably" is what this task removes.

- [ ] **Step 1: Establish which it is, before writing an assertion**

Run:
```bash
grep -rn "pending\|cancel_pending" src/aegis/core/session.py src/aegis/tui/pending.py src/aegis/tui/pane.py | head -30
```
Decide from the code whether the pending buffer is owned by the session (brain) or the pane (view). **Write the answer into your final report**, with the file and line that settles it.

- [ ] **Step 2: Write the test that pins it**

```python
# tests/views/test_pending_is_brain_state.py
"""Text still in the box is yours; text you have sent is everyone's.

A pending message is submitted and awaiting a turn boundary. Per-view it
would show different queues for one agent, and cancelling a chip in one
view would not cancel it in the other.
"""
from aegis.config.roots import AegisRoots
from aegis.core.manager import SessionManager


class _FakeHarness:
    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        if False:
            yield


async def test_a_pending_message_is_owned_by_the_session_not_a_pane(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    mgr = SessionManager({"default": object()}, "default",
                         make_session=lambda p, u, h: _FakeHarness(),
                         mcp=None, roots=roots)
    sess = mgr._sync_spawn("default")
    # The buffer must hang off the session object every view shares, not
    # off any widget. Assert on the substrate, not on a rendered strip.
    assert hasattr(sess, "cancel_pending"), (
        "pending messages must be cancellable on the session — if this "
        "lives on the pane, two views disagree about one agent's queue")
```

- [ ] **Step 3: Run it**

Run: `uv run python -m pytest tests/views/test_pending_is_brain_state.py -v -m "not live"`
Expected: PASS if the buffer is already brain-owned. **If it FAILS, stop and report** — moving the buffer is a larger change than this task budgets, and the coordinator decides whether to widen scope or file it.

- [ ] **Step 4: Commit**

```bash
git add tests/views/test_pending_is_brain_state.py
git commit -m "test(views): pin pending messages as brain state, not view state"
```

---

### Task 8: The stage gate — two views, two geometries, one brain

**Files:**
- Test: `tests/views/test_multi_view.py`

**Interfaces:**
- Consumes: everything above.
- Produces: no code. This is the acceptance test for stage 4.

**What it must prove**, from the spec's testing section: two views at different geometries over one brain; both sizes hold; a tab opened in one appears in the other; and focus, scroll and drafts do **not** cross.

**Assert on the substrate, not on a proxy.** Comparing two `View` objects for non-identity is a proxy that passes while both apps render at one geometry. The size assertion must read what each app actually rendered at, and the no-crossing assertion must read the per-view state, not the registry keys.

- [ ] **Step 1: Write the gate**

```python
# tests/views/test_multi_view.py
"""The stage-4 gate. Two views, one brain.

Deliberately in-process: each view's frames are read out of its own sink,
so a failure here is the seam and never a socket. The transport arrives in
stage 5 and inherits this contract.
"""
from aegis.config.roots import AegisRoots
from aegis.core.manager import SessionManager
from aegis.views.registry import ViewRegistry


class _FakeHarness:
    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        if False:
            yield


def _reg(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    mgr = SessionManager({"default": object()}, "default",
                         make_session=lambda p, u, h: _FakeHarness(),
                         mcp=None, roots=roots)
    return ViewRegistry(manager=mgr, roots=roots), mgr


def _assert_live_driver_is_ours(app):
    """run_test(headless=True) swaps in HeadlessDriver (app.py:3334-3337).

    Without this guard the frame assertions below pass vacuously — an empty
    sink because no ViewDriver ran reads identically to an empty sink
    because frames did not cross, and the second is the only one this gate
    is about.
    """
    from aegis.views.driver import ViewDriver
    assert isinstance(app._driver, ViewDriver), (
        f"view is running on {type(app._driver).__name__}, not ViewDriver — "
        "pass headless=False to run_test")


async def test_both_views_hold_their_own_geometry(tmp_path):
    reg, _ = _reg(tmp_path)
    a = await reg.open("narrow", (80, 24))
    b = await reg.open("wide", (140, 50))
    async with a.app.run_test(headless=False, size=(80, 24)):
        async with b.app.run_test(headless=False, size=(140, 50)):
            _assert_live_driver_is_ours(a.app)
            _assert_live_driver_is_ours(b.app)
            assert a.app.size == (80, 24)
            assert b.app.size == (140, 50)
    await reg.close_all()


async def test_a_session_opened_in_one_view_exists_for_the_other(tmp_path):
    """Tab identity is brain state: opening a tab opens it for everyone."""
    reg, mgr = _reg(tmp_path)
    a = await reg.open("narrow", (80, 24))
    b = await reg.open("wide", (140, 50))
    handle = mgr._sync_spawn("default").handle
    handles = {si.handle for si in mgr.list_sessions()}
    assert handle in handles
    assert a.app.manager is b.app.manager
    await reg.close_all()


async def test_focus_and_drafts_do_not_cross(tmp_path):
    reg, _ = _reg(tmp_path)
    a = await reg.open("narrow", (80, 24))
    b = await reg.open("wide", (140, 50))
    a.state.active_handle = "lucid-knuth"
    a.state.drafts["lucid-knuth"] = "typed in A"
    a.state.scroll["lucid-knuth"] = 42
    assert b.state.active_handle is None
    assert b.state.drafts == {}
    assert b.state.scroll == {}
    await reg.close_all()


async def test_each_view_writes_only_to_its_own_sink(tmp_path):
    """The property the whole seam rests on. If frames cross, every other
    assertion here is coincidence."""
    reg, _ = _reg(tmp_path)
    a = await reg.open("narrow", (80, 24))
    b = await reg.open("wide", (140, 50))
    async with a.app.run_test(headless=False, size=(80, 24)):
        _assert_live_driver_is_ours(a.app)
    assert a.frames, "view A rendered nothing"
    assert not b.frames, "view B received view A's frames"
    await reg.close_all()


async def test_a_reattached_view_gets_a_full_frame(tmp_path):
    """Textual emits incremental updates (_compositor.py:1118), so a client
    reattaching to a live view would otherwise receive deltas against a
    screen it has never seen."""
    reg, _ = _reg(tmp_path)
    v = await reg.open("tty-1", (80, 24))
    async with v.app.run_test(headless=False, size=(80, 24)) as pilot:
        _assert_live_driver_is_ours(v.app)
        v.frames.clear()
        v.repaint()
        await pilot.pause()
    assert v.frames, "repaint() emitted no frame"
    await reg.close_all()
```

- [ ] **Step 2: Run the gate**

Run: `uv run python -m pytest tests/views/test_multi_view.py -v -m "not live"`
Expected: PASS (5 tests)

- [ ] **Step 3: Mutation-check the gate**

A gate that cannot fail is worth less than none, and this one is the
acceptance test for the stage. Three mutations, each must turn it RED:

```bash
# 1. In view.py's open_view, bind every view to one shared module-level
#    list instead of its own `frames`.
#    -> test_each_view_writes_only_to_its_own_sink goes red.
# 2. In open_view, drop `state.geometry = geometry` and let the restored
#    value win.
#    -> test_both_views_hold_their_own_geometry goes red at the second attach.
# 3. In View.repaint, replace the compositor lines with
#    `self.app.refresh(repaint=True, layout=True)`.
#    -> test_a_reattached_view_gets_a_full_frame goes red. (This is the
#       measured finding from the spec: refresh() emits nothing.)
```

Record the RED output for each, then revert. **Mutation 3 is the one most
likely to be "simplified" back by a later contributor**, which is exactly
why it is pinned.

- [ ] **Step 4: Run the full suite**

Run: `uv run python -m pytest -q -m "not live"`
Expected: **3592 + this plan's new tests passed, 1 skipped, rc=0.** Read the
rc directly; never through a pipe.

- [ ] **Step 5: Commit**

```bash
git add tests/views/test_multi_view.py
git commit -m "test(views): the stage-4 gate — two views, two geometries, one brain"
```

---

## Done when

- `uv run pytest -q -m "not live"` is green, with no regression against the 3592 baseline.
- `tests/views/test_multi_view.py` passes **and** fails under each of its three mutations.
- `aegis`, `aegis serve`, `aegis web` and `--remote` are observably unchanged — this plan deletes nothing and alters no existing behaviour.
- `Workspace` carries no `active_handle`, and an old workspace file still loads.

## Deliberately not in this plan

- **Any transport.** No unix socket, no WebSocket, no `aegis attach`. See *Scope decision* above.
- **Daemon lifecycle** — autostart, idle timeout, `aegis ls` / `aegis kill`. Stage 5.
- **The auth change** — dropping Caddy `basicauth` for the one-secret handshake. Stage 5, and the spec is emphatic that removing `basicauth` from a live public hostname must land in the *same* change as the cookie exchange, never before it.
- **Deletion** of the web client, WS plane, `RemoteSessionManager`, `ws_client` and `--remote`. Stage 6, after 4 and 5 are proven live.

## Open questions stage 5 must settle, recorded here so they are not lost

These are the spec's own four, none of which stage 4 forces:

1. **Idle-timeout duration** for daemon self-reaping, and whether zero-agents is the right second condition alongside zero-views.
2. **Geometry with zero views attached.** A brain with no views has no screen; the first view to attach defines its own. Stage 4 sidesteps this because a `View` always brings a geometry — confirm nothing in the TUI assumes a size before the first attach.
3. **Voice with N views.** One microphone, many views: `VoiceStrip` needs an explicit owner or push-to-talk is ambiguous.
4. **Tab order on reorder.** Order is brain state, so reordering in one view reorders for everyone. Confirm that is wanted, or move it view-side.
