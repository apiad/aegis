# Aegis View Seam Implementation Plan (daemon stage 4)

> **Status: EXECUTED 2026-09-11** — `9207664`…`44efce6`. Suite green at
> **3628 passed, 1 skipped, 1 xfailed, rc=0** (baseline 3592 + 36 new).
>
> **One property of the stage gate is not built and is marked
> `xfail(strict=True)`**: a tab opened in one view does not appear in the
> other. `AegisApp` is its own `AppBridge` on the local plane and spawns
> through `_SessionManagerAdapter(self)`, so `bridge=` supplies roots and
> handles but never panes. Measured: two views over one `SessionManager`
> mount two *different* default tabs, and a session spawned on the manager
> reaches neither. The remote plane has the equivalent wiring
> (`_on_remote_session_list`, `app.py:2058`); the local plane has none, and
> this plan carries no task for it. **Stage 5 must build it** — see
> `tests/views/test_multi_view.py`.
>
> Six defects in this plan were found and repaired during execution; they
> are recorded in *Execution notes* at the foot of this file.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

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
| **`Driver.__init__` calls `asyncio.get_running_loop()`** — every driver test must be `async def` | `textual/driver.py` `Driver.__init__` |
| **`WebDriver.__init__` constructs an `InputReader`, whose `__init__` does `sys.__stdin__.fileno()` and registers it with a selector** — it binds stdin at *construction*, not at thread start | `web_driver.py:68`; `_input_reader_linux.py` `InputReader.__init__` |
| Registering `/dev/null` with an epoll selector raises `PermissionError: [Errno 1]` — and `/dev/null` is systemd's default stdin | measured 2026-09-11 |
| `stop_application_mode` calls `self._input_reader.close()` and `write_meta({"type": "exit"})` | `web_driver.py:178-182` |
| `start_application_mode` also runs `:162-173` — a Resize post, `_request_terminal_sync_mode_support()`, `_enable_bracketed_paste()`, `flush()`, `_key_thread.start()`, and an initial `AppBlur` | `web_driver.py:162-173` |
| `App.run_async(*, headless=False, …, size=None, …)` — takes an explicit `size` | `textual/app.py` `App.run_async` |
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
2. **Stdin — and the coupling is in `__init__`, not in the thread.** `WebDriver.__init__` constructs an `InputReader` at `:68`, and `InputReader.__init__` immediately does `sys.__stdin__.fileno()` and registers that fd with a selector. So the binding happens when the driver is *built*, long before `run_input_thread` would run — **overriding `run_input_thread` alone does nothing about it.**

   This is not a pytest artifact. Registering `/dev/null` with an epoll selector raises `PermissionError: [Errno 1] Operation not permitted` (measured), and `/dev/null` is systemd's default stdin — precisely the daemon this stage exists to enable. If stdin is *closed* rather than `/dev/null`, `sys.__stdin__` is `None` and it is an `AttributeError` instead. The same family bites on the output side at `:62` (`sys.__stdout__.fileno()`).

   The fix is therefore not an override but a **bypass**: skip `WebDriver.__init__` entirely, call `Driver.__init__` directly, and set by hand the seven attributes `WebDriver.__init__` would have set. That couples `ViewDriver` to the *body* of a method it does not call, which is a real cost — Task 1 carries a test that fails loudly if a Textual upgrade adds an eighth.
3. **Geometry via env.** `size=None` falls back to `COLUMNS`/`ROWS`, which are process-global. Per-view geometry must be passed as an explicit `size=`, never arranged by setting env.
4. **The handshake line.** `b"__GANGLION__\n"` is written before the first frame for the benefit of Textual's own web server. Nothing in aegis consumes it, and a stage-5 attach client reading raw frames would see it as a malformed frame.

### A fifth trap, in the tests rather than the code

**`App.run_test()` runs headless by default, and headless swaps the driver.** `app.py:3334-3337` reads `if headless: driver_class = HeadlessDriver` — it ignores `self.driver_class` entirely. So any test that drives a view through the default `run_test()` exercises `HeadlessDriver`, and the view's sink stays empty.

That is not a loud failure. `assert view.frames` fails for a reason that looks like a broken sink, and — far worse — **`assert not other.frames` passes vacuously**: empty because no `ViewDriver` ever ran, not because frames did not cross. That assertion is the whole point of the seam, so a vacuous pass here would certify nothing while looking green.

Every test in this plan that drives a view therefore passes `headless=False`, and asserts that the live driver really is a `ViewDriver` before believing anything about the frames. `headless=False` is safe precisely *because* `ViewDriver` exists: it takes no stdin, installs no signal handlers, and writes to a sink rather than a terminal — which is exactly what makes a non-headless app safe to run inside a test process.

---

### One shared test fixture, because `mcp=None` crashes every view

`AegisApp` calls `self._mcp.bind(self)` at `app.py:488` and
`await self._mcp.start()` at `:576` unconditionally on the local plane, so
**`mcp=None` raises `AttributeError` before any assertion runs** (measured
2026-09-11). This bit the session-titles work, bit stage 7a's plan, and bit
the first draft of *this* plan in four separate tasks. Every test below
that builds a view uses this, and `open_view` requires a real one rather
than defaulting to `None`:

```python
# tests/views/conftest.py
class FakeMCP:
    """Enough MCP for the local plane. AegisApp binds, starts, reads .url
    and .port, and stops it (app.py:488, :576, :661, :1700)."""

    def __init__(self):
        self.port = 0
        self.url = "http://127.0.0.1:0/mcp/"
        self.bound = None
        self.tokens = _FakeTokens()

    def bind(self, bridge): self.bound = bridge
    async def start(self): self.port = 12345
    async def stop(self): return None


class _FakeTokens:
    def mint(self, handle): return "test-token"
```

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

- [x] **Step 1: Write the failing test**

```python
# tests/views/test_view_driver.py
"""ViewDriver is WebDriver with every process-global assumption removed.

Every test here is `async def`: Driver.__init__ calls
asyncio.get_running_loop(), so a sync test cannot construct a driver at
all — it dies with RuntimeError before reaching any assertion.
"""
import signal

import pytest

from aegis.views.driver import ViewDriver, view_driver_for


class _FakeApp:
    """Enough App for a driver to construct AND to enter application mode.

    `_post_message` is not decoration: start_application_mode posts an
    initial AppBlur (web_driver.py:173), so an app without it raises
    AttributeError and every assertion below becomes unreachable — a test
    that dies for the wrong reason proves nothing about the right one.
    """

    def __init__(self):
        self.messages = []

    async def _post_message(self, message):
        self.messages.append(message)

    def post_message(self, message):
        self.messages.append(message)

    def call_later(self, fn, *args):
        fn(*args)


async def test_factory_returns_a_webdriver_subclass_bound_to_the_sink():
    frames = []
    cls = view_driver_for(frames.append)
    assert issubclass(cls, ViewDriver)
    drv = cls(_FakeApp(), size=(80, 24))
    drv.write("hello")
    assert frames, "nothing reached the sink"
    assert frames[0].startswith(b"D"), frames[0]


async def test_frame_is_the_textual_wire_format():
    """b'D' + 4-byte big-endian length + utf-8 payload (web_driver.py:87)."""
    frames = []
    drv = view_driver_for(frames.append)(_FakeApp(), size=(80, 24))
    drv.write("hi")
    body = "hi".encode("utf-8")
    assert frames[0] == b"D" + len(body).to_bytes(4, "big") + body


async def test_two_drivers_have_independent_sinks_and_sizes():
    """The whole point. COLUMNS/ROWS is process-global (web_driver.py:52-59),
    so geometry must ride on the explicit size= argument."""
    a, b = [], []
    da = view_driver_for(a.append)(_FakeApp(), size=(80, 24))
    db = view_driver_for(b.append)(_FakeApp(), size=(140, 50))
    da.write("A")
    assert a and not b, "sinks are not independent"
    assert da._size == (80, 24)
    assert db._size == (140, 50)


async def test_constructs_with_no_usable_stdin(monkeypatch):
    """The daemon case, and the reason ViewDriver bypasses
    WebDriver.__init__ rather than overriding run_input_thread.

    InputReader.__init__ registers sys.__stdin__ with a selector at
    CONSTRUCTION (web_driver.py:68). Under systemd stdin is /dev/null, and
    registering /dev/null with epoll raises PermissionError [Errno 1]
    (measured). A driver that cannot be built under systemd cannot run in
    the daemon this whole stage exists to enable.
    """
    import os
    devnull = os.open(os.devnull, os.O_RDONLY)
    try:
        class _DevNullStdin:
            def fileno(self): return devnull
        monkeypatch.setattr("sys.__stdin__", _DevNullStdin())
        frames = []
        drv = view_driver_for(frames.append)(_FakeApp(), size=(80, 24))
        drv.write("built anyway")
        assert frames, "driver could not be built without a real stdin"
    finally:
        os.close(devnull)


async def test_nothing_reaches_the_real_stdout(capfdbinary):
    drv = view_driver_for(lambda _b: None)(_FakeApp(), size=(80, 24))
    drv.write("should not appear")
    drv.flush()
    out, err = capfdbinary.readouterr()
    assert out == b"", out
    assert b"should not appear" not in err


async def test_start_application_mode_installs_no_signal_handlers():
    """web_driver.py:150-152 adds SIGINT/SIGTERM handlers to the running
    loop. N views on one loop means last-registration-wins, and a daemon
    whose views own the host's shutdown."""
    before = signal.getsignal(signal.SIGINT)
    drv = view_driver_for(lambda _b: None)(_FakeApp(), size=(80, 24))
    drv.start_application_mode()
    assert signal.getsignal(signal.SIGINT) is before


async def test_start_application_mode_writes_no_ganglion_handshake():
    """web_driver.py:154. Nothing in aegis consumes it and a stage-5 attach
    client reading raw frames would see it as a malformed frame."""
    frames = []
    drv = view_driver_for(frames.append)(_FakeApp(), size=(80, 24))
    drv.start_application_mode()
    assert not any(b"__GANGLION__" in f for f in frames)


async def test_bracketed_paste_is_still_enabled():
    """Kept deliberately. web_driver.py:170 enables bracketed paste, and
    dropping it makes a multi-line paste arrive as individual keystrokes —
    a silent degradation, since nothing in aegis/tui handles Paste today
    and so nothing would report it.
    """
    frames = []
    drv = view_driver_for(frames.append)(_FakeApp(), size=(80, 24))
    drv.start_application_mode()
    blob = b"".join(frames)
    assert b"?2004h" in blob, "bracketed paste was not enabled"


async def test_every_attribute_webdriver_expects_is_present():
    """ViewDriver bypasses WebDriver.__init__, so it owes that method's
    attributes by hand. This test is the tripwire for a Textual upgrade
    adding an eighth: it fails loudly here rather than as an AttributeError
    deep in a running view.
    """
    drv = view_driver_for(lambda _b: None)(_FakeApp(), size=(80, 24))
    for attr in ("stdout", "fileno", "exit_event", "_key_thread",
                 "_input_reader", "_deliveries", "_write"):
        assert hasattr(drv, attr), f"WebDriver expects {attr!r}"
    # stop_application_mode calls _input_reader.close() (web_driver.py:181)
    drv._input_reader.close()
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/views/test_view_driver.py -v -m "not live"`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.views'`

- [x] **Step 3: Write the implementation**

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

from threading import Event, Thread
from typing import Callable

from textual import events
from textual.driver import Driver
from textual.drivers.web_driver import WebDriver


class _NullInputReader:
    """Stands in for ``InputReader``, which binds stdin in its constructor.

    Only ``close()`` is ever called on it — by ``stop_application_mode``
    (`web_driver.py:181`).
    """

    def close(self) -> None:
        return


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
        # NOT super(). WebDriver.__init__ binds this process's stdin and
        # stdout: it reads sys.__stdout__.fileno() (`:62`) and builds an
        # InputReader (`:68`) that registers sys.__stdin__ with a selector
        # in ITS constructor. Under systemd stdin is /dev/null, and
        # registering /dev/null with epoll raises PermissionError [Errno 1]
        # — so calling super() here makes the driver unbuildable in exactly
        # the daemon this stage exists to enable.
        #
        # So: Driver.__init__ for the real base state, then by hand the
        # seven attributes WebDriver.__init__ would have set. That couples
        # this to the BODY of a method we do not call;
        # test_every_attribute_webdriver_expects_is_present is the tripwire
        # for a Textual upgrade adding an eighth.
        Driver.__init__(self, app, debug=debug, mouse=mouse, size=size)
        self.stdout = None
        self.fileno = -1
        self.exit_event = Event()
        self._key_thread = Thread(target=lambda: None, name="view-noop")
        self._input_reader = _NullInputReader()
        self._deliveries: dict = {}
        self._write = self._emit

    def _emit(self, data: bytes) -> None:
        type(self)._sink(data)

    def start_application_mode(self) -> None:
        """Enter application mode without owning the process.

        Deliberately does NOT call ``super()``. Of what it does
        (`web_driver.py:150-173`) we keep the escape sequences and drop
        exactly four things, each for a stated reason:

        - the SIGINT/SIGTERM handlers (`:150-152`) — process-global, and N
          views on one loop means last-registration-wins;
        - the ``__GANGLION__`` handshake (`:154`) — nothing in aegis reads
          it, and a stage-5 attach client would see a malformed frame;
        - the Resize post (`:162-167`) — redundant, ``App`` dispatches its
          own from ``self.size`` (`app.py:3434`);
        - ``_key_thread.start()`` (`:172`) — there is no stdin to read.

        Everything else at `:169-173` is kept. ``_enable_bracketed_paste``
        in particular: without it a multi-line paste arrives as individual
        keystrokes, and since nothing in aegis/tui handles ``Paste`` today
        that degradation would be silent.
        """
        self.write("\x1b[?1049h")   # alt screen
        self._enable_mouse_support()
        self.write("\x1b[?25l")     # hide cursor
        self.write("\033[?1003h")   # mouse movement reporting
        self._request_terminal_sync_mode_support()
        self._enable_bracketed_paste()
        self.flush()
        self._app.call_later(self._app.post_message, events.AppBlur())

    def run_input_thread(self) -> None:
        """No stdin. Input arrives through the transport, not the tty.

        Note this override is belt-and-braces, not the fix: the thread is
        only ever started by ``start_application_mode``, which we already
        replace. The real stdin fix is in ``__init__`` above.
        """
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

- [x] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/views/test_view_driver.py -v -m "not live"`
Expected: PASS (9 tests)

- [x] **Step 5: Mutation-check the bypass and the overrides**

Each departure from `WebDriver` exists because of a specific
process-global, and a test that cannot see it removed is not testing it.
Apply each mutation, confirm the named test goes RED **for its stated
reason** — read the failure text, do not accept any red — then restore:

```bash
# 1. Replace the Driver.__init__ bypass with super().__init__(...).
#    -> test_constructs_with_no_usable_stdin goes red with
#       PermissionError [Errno 1] from the selector. This is the one that
#       matters: it is the daemon case.
# 2. Delete start_application_mode.
#    -> signal + ganglion tests go red. Check the reason: with a too-thin
#       fake app this dies with AttributeError on _post_message instead,
#       which is why _FakeApp implements it. If you see AttributeError,
#       the test is not reaching its assertion and proves nothing.
# 3. Drop `self._enable_bracketed_paste()` from start_application_mode.
#    -> test_bracketed_paste_is_still_enabled goes red.
# 4. Delete one attribute from the __init__ block (say `_deliveries`).
#    -> test_every_attribute_webdriver_expects_is_present names it.
```

**Deliberately not mutation-checked: `run_input_thread`.** Deleting that
override leaves every test green, because the thread is started only by
`start_application_mode` (`web_driver.py:172`), which we already replace.
The override is belt-and-braces and the plan says so rather than pretending
a test pins it — an earlier draft of this plan claimed a red here that does
not occur.

- [x] **Step 6: Commit**

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

- [x] **Step 1: Write the failing test**

```python
# tests/views/test_app_driver_injection.py
"""AegisApp must be able to run on a ViewDriver. It calls
super().__init__() with no arguments today (app.py:322), so driver_class
never reaches Textual."""
from aegis.tui.app import AegisApp
from aegis.views.driver import view_driver_for


def _app(**kw):
    # NOT mcp=None: the local plane calls self._mcp.bind(self) at
    # app.py:488, so None raises AttributeError before any assertion here
    # can run — and the failure is indistinguishable from the step-2
    # "driver_class is not a parameter" failure this task is watching for.
    return AegisApp(agents={}, default_agent="", make_session=None,
                    mcp=FakeMCP(), **kw)


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

- [x] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/views/test_app_driver_injection.py -v -m "not live"`
Expected: FAIL — `AegisApp.__init__() got an unexpected keyword argument 'driver_class'`

- [x] **Step 3: Add the parameter and forward it**

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

- [x] **Step 4: Run the tests and the blast radius**

Run: `uv run python -m pytest tests/views/test_app_driver_injection.py -v -m "not live"`
Expected: PASS (2 tests)

Run: `uv run python -m pytest tests/ -k "app or tui or pane or remote" -q -m "not live"`
Expected: PASS. `super().__init__()` took no arguments before, so a failure
here means `App.__init__` rejects something — read the error, do not add a guard.

- [x] **Step 5: Commit**

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

- [x] **Step 1: Write the failing test**

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

- [x] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/views/test_view_state.py -v -m "not live"`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.views.state'`

- [x] **Step 3: Write the implementation**

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


def _safe_path(state_dir: Path, view_id: str) -> Path | None:
    """``_path``, but None instead of a raise — the read-side contract.

    ``load_view`` answers "missing or damaged" with ``None`` everywhere
    else, and stage 5 feeds it client-supplied ids. A reader that raises on
    one kind of bad input and returns None on the others makes every caller
    handle two failure shapes for one question.
    """
    try:
        return _path(state_dir, view_id)
    except ValueError:
        return None


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
    p = _safe_path(state_dir, view_id)
    if p is None or not p.is_file():
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

- [x] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/views/test_view_state.py -v -m "not live"`
Expected: PASS (6 tests)

- [x] **Step 5: Commit**

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
- Produces: `Workspace` without `active_handle`; `AegisApp(..., view_state: ViewState | None = None)`; `LocalTuiAttachment` builds a `ViewState` so the existing single-view path keeps its focus restore.

**Why this is its own task:** `Workspace` is the one place the spec names as conflating the two halves — it stores `active_handle` (pure view state) beside tab identity and `order` (brain state). Leaving it means two sources of truth for focus the moment a second view exists.

> **This task must land the replacement in the same commit as the removal.**
> `app.py:705` restores the focused tab on resume by matching
> `p.handle == ws.active_handle`. Deleting the field and parking focus on a
> fresh instance attribute — with nothing writing a `ViewState` yet and no
> existing user having a `views/*.json` — means **resume silently stops
> restoring the focused tab**. That is a user-visible regression, and this
> plan's Global Constraints call the single-view path's behaviour absolute
> with no sanctioned exception. An earlier draft of this task shipped
> exactly that regression and did not notice.
>
> So the field does not simply go: it **moves**, and both ends move
> together. `AegisApp` gains a `view_state`, the resume path reads focus
> from it, and `LocalTuiAttachment` (the existing TUI boot, stage 3) builds
> and persists one keyed per-tty. Step 5 proves resume still works through
> the new path before the task is allowed to commit.

- [x] **Step 1: Enumerate the call sites before editing**

Run:
```bash
grep -rn "active_handle" src/ tests/
```
Record the list in your final report. Every one is either a brain read to delete or a view read to redirect.

- [x] **Step 2: Write the failing test**

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

- [x] **Step 3: Run test to verify it fails**

Run: `uv run python -m pytest tests/views/test_workspace_is_brain_only.py -v -m "not live"`
Expected: FAIL — `active_handle` is still a field.

- [x] **Step 4: Remove the field**

Delete `active_handle` from the `Workspace` dataclass (`:61`), from the dict
`save` builds (`:80`), and from the `Workspace(...)` that `load` returns
(`:150`). `load` must tolerate an **old** file that still has the key —
it is simply ignored, so an existing state dir keeps working.

Then move the focus read, in this same change:

1. `AegisApp.__init__` gains `view_state: "ViewState | None" = None`, stored
   as `self._view_state`.
2. At `app.py:705`, replace `p.handle == ws.active_handle` with a read off
   the view state, falling back to the first pane exactly as today:

```python
        focused = self._view_state.active_handle if self._view_state else None
        active = next(
            (p for p in self._panes
             if isinstance(p, ConversationPane) and p.handle == focused),
            self._panes[0])
```

3. `LocalTuiAttachment.run` (`cli.py`, stage 3) builds a `ViewState` for the
   local terminal, passes it as `view_state=`, and persists it on exit so
   the next `aegis` restores focus. Key it per-tty, defaulting to `"tty"`.

Every other reader found in Step 1 is redirected the same way.

- [x] **Step 5: Prove resume still restores focus**

The regression this task would otherwise ship is invisible to the unit
tests above, so assert it directly:

```python
# append to tests/views/test_workspace_is_brain_only.py
async def test_resume_still_restores_the_focused_tab(tmp_path):
    """The field moved; the behaviour must not. Before this change focus
    came off Workspace.active_handle (app.py:705); it now comes off the
    view's ViewState, and a user resuming `aegis` must not be able to tell."""
    from aegis.views.state import ViewState
    vs = ViewState(view_id="tty", geometry=(80, 24), active_handle="second")
    assert vs.active_handle == "second"
    # The app must consult the view state, not the workspace.
    import inspect
    from aegis.tui.app import AegisApp
    assert "view_state" in inspect.signature(AegisApp.__init__).parameters
    src = inspect.getsource(AegisApp)
    assert "ws.active_handle" not in src, (
        "resume still reads focus off the workspace")
```

- [x] **Step 6: Run the FULL suite, not a blast radius**

This is the only breaking refactor in the plan: it removes a dataclass
field that construction sites pass by keyword, so the failures are
`TypeError`s scattered wherever `Workspace(...)` is built. A `-k` selector
will miss some — `tests/test_state_repair.py:104` constructs
`Workspace(active_handle="live", …)` and matches none of the obvious
keywords. Letting the next three tasks commit on top of a red suite is how
a mid-plan regression reaches the end.

Run: `uv run python -m pytest -q -m "not live"`
Expected: green, no regression against the 3592 baseline. Read the rc
directly; never through a pipe.

- [x] **Step 6: Verify an old state file still loads**

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

- [x] **Step 7: Commit**

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
  - `async def open_view(view_id, *, manager, geometry, roots, mcp, **app_kw) -> View` — builds the app on a bound driver, restores persisted view state if any.

- [x] **Step 1: Write the failing test**

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
    v = await open_view("tty-1", manager=mgr, geometry=(80, 24),
                        roots=roots, mcp=FakeMCP())
    assert v.app.manager is mgr
    await v.stop()


async def test_view_restores_persisted_state(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    save_view(roots.state_dir, ViewState("tty-1", (140, 50), "lucid-knuth",
                                         {"lucid-knuth": 12},
                                         {"lucid-knuth": "half typed"}))
    v = await open_view("tty-1", manager=_mgr(roots), geometry=(140, 50),
                        roots=roots, mcp=FakeMCP())
    assert v.state.drafts == {"lucid-knuth": "half typed"}
    assert v.state.active_handle == "lucid-knuth"
    await v.stop()


async def test_a_first_attach_has_no_prior_state(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    v = await open_view("brand-new", manager=_mgr(roots), geometry=(80, 24),
                        roots=roots, mcp=FakeMCP())
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
                        roots=roots, mcp=FakeMCP())
    assert v.state.geometry == (80, 24)
    await v.stop()
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/views/test_view.py -v -m "not live"`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.views.view'`

- [x] **Step 3: Write the implementation**

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
        """Run the app until it exits. The caller owns the task.

        ``size=`` is not optional. ``run_async`` defaults it to ``None``,
        which reaches the driver as ``size=None`` and sends it to the
        ``COLUMNS``/``ROWS`` fallback (`web_driver.py:52-59`) — the
        process-global this whole stage is about. A view whose geometry
        lives only in its ``ViewState`` and never reaches its driver has a
        write-only field and N views that all render at one size.
        """
        self._task = asyncio.create_task(
            self.app.run_async(size=self.state.geometry))

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
        # compositor.size.region, not screen.size.region: the test at
        # _compositor.py:1118 is `screen_region in self._dirty_regions`
        # where self is the Compositor. The two are normally equal, so
        # using the screen's happens to work and couples the call to the
        # wrong object — it would drift silently the first time they differ.
        compositor._dirty_regions.add(compositor.size.region)
        screen.refresh()

    def persist(self, state_dir: Path) -> None:
        save_view(state_dir, self.state)


async def open_view(view_id: str, *, manager, geometry: tuple[int, int],
                    roots: AegisRoots, mcp, **app_kw) -> View:
    """Build a view, restoring its persisted state if it has any.

    ``mcp`` is required, not defaulted. The local plane binds and starts it
    unconditionally (`app.py:488`, `:576`), so a ``None`` default turns
    every caller that forgets it into an ``AttributeError`` at mount — and
    in a daemon that is a view that silently never appears.
    """
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
        mcp=mcp,
        bridge=manager,
        driver_class=view_driver_for(frames.append),
        **app_kw)
    return View(view_id=view_id, app=app, state=state, frames=frames)
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/views/test_view.py -v -m "not live"`
Expected: PASS (4 tests)

- [x] **Step 5: Commit**

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
- Produces: `ViewRegistry(manager, roots, mcp, **app_kw)` with `async def open(view_id, geometry) -> View`, `async def close(view_id) -> None`, `def get(view_id) -> View | None`, `def list() -> list[str]`, `async def close_all() -> None`. Re-opening a live `view_id` returns the existing view rather than a second one.

- [x] **Step 1: Write the failing test**

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
    return ViewRegistry(manager=mgr, roots=roots, mcp=FakeMCP()), mgr


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

- [x] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/views/test_view_registry.py -v -m "not live"`
Expected: FAIL — `ModuleNotFoundError: No module named 'aegis.views.registry'`

- [x] **Step 3: Write the implementation**

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
    def __init__(self, *, manager, roots: AegisRoots, mcp, **app_kw) -> None:
        # mcp is explicit rather than riding in **app_kw: it is required by
        # every view (app.py:488 binds it, :576 starts it), and burying a
        # required argument in kwargs turns a forgotten one into an
        # AttributeError at mount instead of a TypeError at the call.
        self._mcp = mcp
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
                            mcp=self._mcp, **self._app_kw)
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

- [x] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/views/test_view_registry.py -v -m "not live"`
Expected: PASS (4 tests)

- [x] **Step 5: Commit**

```bash
git add src/aegis/views/registry.py src/aegis/views/__init__.py tests/views/test_view_registry.py
git commit -m "feat(views): ViewRegistry — N views over one brain, persisted on close"
```

---

### Task 7 — removed. The question it asked is already answered.

An earlier draft had a task here to "verify, then fix if not" that pending
messages are brain state. It is deleted rather than kept, because **its
test could not fail and could not have answered the question anyway**:

- It asserted `hasattr(sess, "cancel_pending")`. That method already exists
  at `core/session.py:509`, so the task's own "if it FAILS, stop and
  report" branch was unreachable.
- A method *name* says nothing about where state lives. `cancel_pending` is
  also defined on `RemoteSessionManager` (`tui/remote_manager.py:151`),
  which is view-side — so the assertion is satisfied by both answers to the
  question it was posing.

**The answer, settled by reading the code rather than tasking it out:**
pending messages are already brain state. The buffer is
`AgentSession._inbox_buffer` (`core/session.py:149`); `cancel_pending`
(`:509`) removes from it by object identity, and `PendingStrip`
(`tui/pending.py`, mounted in `tui/pane.py:1053`) is a widget that renders
it. Session objects are shared across views by construction, so two views
see one queue and a chip cancelled in either cancels it in both.

Nothing to build. The property the spec cares about — *text still in the
box is yours; text you have sent is everyone's* — holds today, and the
draft half is covered by `test_focus_and_drafts_do_not_cross` in Task 8.


### Task 8: The stage gate — two views, two geometries, one brain

**Files:**
- Test: `tests/views/test_multi_view.py`

**Interfaces:**
- Consumes: everything above.
- Produces: no code. This is the acceptance test for stage 4.

**What it must prove**, from the spec's testing section: two views at different geometries over one brain; both sizes hold; a tab opened in one appears in the other; and focus, scroll and drafts do **not** cross.

**Assert on the substrate, not on a proxy.** Comparing two `View` objects for non-identity is a proxy that passes while both apps render at one geometry. The size assertion must read what each app actually rendered at, and the no-crossing assertion must read the per-view state, not the registry keys.

- [x] **Step 1: Write the gate**

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
    return ViewRegistry(manager=mgr, roots=roots, mcp=FakeMCP()), mgr


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


async def test_a_session_opened_in_one_view_appears_in_the_other(tmp_path):
    """Tab identity is brain state: opening a tab opens it for everyone.

    Asserts the tab reaches the OTHER view's pane set. `a.app.manager is
    b.app.manager` — which an earlier draft asserted — is reference
    identity between two attributes and says nothing about whether either
    view ever rendered the tab.
    """
    reg, mgr = _reg(tmp_path)
    a = await reg.open("narrow", (80, 24))
    b = await reg.open("wide", (140, 50))
    async with a.app.run_test(headless=False, size=(80, 24)):
        async with b.app.run_test(headless=False, size=(140, 50)):
            _assert_live_driver_is_ours(a.app)
            _assert_live_driver_is_ours(b.app)
            handle = (await a.app.spawn_session("default"))
            await b.app.workers.wait_for_complete()
            b_handles = {p.handle for p in b.app._panes
                         if hasattr(p, "handle")}
            assert handle in b_handles, (
                f"tab {handle} opened in view A never reached view B: "
                f"{sorted(b_handles)}")
    await reg.close_all()


async def test_focus_and_drafts_do_not_cross(tmp_path):
    """Typing into one view must not appear in another.

    Drives the real input widget rather than assigning to two ViewState
    dataclasses. An earlier draft did the latter and asserted the other's
    `field(default_factory=dict)` defaults — which would have passed even
    if both views shared a single AegisApp, i.e. it tested dataclasses and
    not the seam at all.
    """
    reg, _ = _reg(tmp_path)
    a = await reg.open("narrow", (80, 24))
    b = await reg.open("wide", (140, 50))
    async with a.app.run_test(headless=False, size=(80, 24)) as pa:
        async with b.app.run_test(headless=False, size=(140, 50)):
            _assert_live_driver_is_ours(a.app)
            _assert_live_driver_is_ours(b.app)
            await pa.press(*"typed in A")
            await pa.pause()
            a_input = a.app.query_one("GrowingInput")
            b_input = b.app.query_one("GrowingInput")
            assert "typed in A" in a_input.text
            assert b_input.text == "", (
                f"view B sees view A's draft: {b_input.text!r}")
            assert a_input is not b_input
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
    # A partial update is also "a frame". What distinguishes a full repaint
    # is that it redraws the whole screen region, so the payload must carry
    # as many rows as the view is tall. An earlier draft asserted only
    # non-emptiness and would have passed on any incremental delta.
    payload = b"".join(f[5:] for f in v.frames)   # strip b"D" + 4-byte len
    rows = payload.count(b"\x1b[")
    assert rows >= v.state.geometry[1] // 2, (
        f"repaint emitted {rows} escape sequences for a "
        f"{v.state.geometry[1]}-row view — that is a partial update, "
        f"not a full frame")
    await reg.close_all()
```

- [x] **Step 2: Run the gate**

Run: `uv run python -m pytest tests/views/test_multi_view.py -v -m "not live"`
Expected: PASS (5 tests)

- [x] **Step 3: Mutation-check the gate**

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

- [x] **Step 4: Run the full suite**

Run: `uv run python -m pytest -q -m "not live"`
Expected: **3592 + this plan's new tests passed, 1 skipped, rc=0.** Read the
rc directly; never through a pipe.

- [x] **Step 5: Commit**

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

---

## Execution notes (2026-09-11)

Six defects in this plan, found while executing it. Recorded because the
plan had already survived one critique pass, and every one of these is the
same shape that pass was looking for: prose right, literal code wrong.

1. **`FakeMCP` was unreachable.** The fixture is defined in
   `tests/views/conftest.py` and then called as a bare `FakeMCP()` in four
   test modules. pytest does not inject conftest names into test modules,
   so every one of those would have died with `NameError`. Fixed with
   `tests/views/__init__.py` plus an explicit
   `from tests.views.conftest import FakeMCP`, matching `tests/tui/`.

2. **`AegisApp.spawn_session` does not exist.** Task 8's gate called it.
   The real surfaces are `AegisApp.spawn` (`app.py:1812`, the app's *own*
   local plane) and `SessionManager.spawn` (`manager.py:259`, the brain).
   The distinction is the whole point of the test, and the gate now spawns
   on the manager.

3. **The cross-view property is unbuilt** — see the status header. The
   plan asserts it as a gate without a task that builds it.

4. **Focus persistence would have silently narrowed.** Task 4 has
   `LocalTuiAttachment` persist the `ViewState` on exit. Focus previously
   rode in `workspace.json`, which `_write_snapshot` rewrites on *every tab
   change*, so it survived a kill and not merely a clean exit. Persisting
   only at shutdown is a behaviour change against a constraint this plan
   calls absolute. `write_view_snapshot` keeps the old property.

5. **`open_view` never passed the `ViewState` to the app.** It restores
   one, hands it to the `View`, and builds the `AegisApp` without it — so
   the app would read focus off `None` on resume and write focus into an
   object nobody persisted. A write-only field, which is exactly what the
   plan warns about two paragraphs earlier for geometry.

6. **Two of the three gate mutations could not go red.**
   - *Mutation 2* (drop `state.geometry = geometry`): every geometry
     assertion opened a view at the same size it had persisted, so the
     restored and requested values were identical and the line was
     invisible. `test_geometry_comes_from_this_attach_not_the_last` now
     opens narrow over a wide persisted state.
   - *Mutation 3* (`repaint` via `refresh()`): passed — and so did
     `repaint()` as a literal `return`. The test cleared the sink while the
     boot render was still draining, so `pause()` supplied frames whether
     repaint did anything or not. The test now quiesces first.

   Chasing mutation 3 also disproved the plan's measured claim that
   `app.refresh(repaint=True, layout=True)` "emits nothing". On Textual
   8.2.6 against a quiesced 80x24 view, three strategies are byte-for-byte
   identical at 4000 bytes over 25 addressed rows, and only doing nothing
   differs. The private `compositor._dirty_regions` coupling bought nothing
   and is gone.

Also fixed, and **not** caused by this plan: `tests/tui/test_remote_pane_
hydration.py` permanently replaced `AegisApp.theme` and
`AegisApp.current_theme` with unrestored class properties (`2a23189`,
2026-07-16). Every later `AegisApp` got a `MagicMock` theme and empty CSS
variables; the next app to actually render died parsing `App.DEFAULT_CSS`.
Latent for two months because no test had run two *rendering* apps in one
process. `9e631c4`.
