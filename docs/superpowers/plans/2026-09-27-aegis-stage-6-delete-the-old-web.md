# Stage 6: delete the old web client — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status: not started.** Written 2026-09-27 against `c4aed3c`, the commit that
finished stage 5b.

**Goal:** Remove the aegis-aware web layer, `RemoteSessionManager`, `ws_client`
and `aegis --remote` from the tree, and deploy the VPS as `aegis-server.service`
plus `aegis-web.service` with Caddy's `basicauth` dropped.

**Architecture:** Stage 5b left the old browser client in the tree, unwired:
nothing imports `src/aegis/web/` any more. This plan deletes it, together with
the two other consumers of its WebSocket protocol — `tui/remote_manager.py` and
`tui/ws_client.py` behind the `aegis --remote` flag. `--remote` is not a
separate module but 20 `hasattr(self, "_remote_manager")` branches threaded
through `tui/app.py` and `tui/pane.py`, so the bulk of the work is collapsing
each branch onto its local side, not deleting files. Then two systemd units
replace one, and the token in `aegis web`'s cookie exchange replaces Caddy's
basic auth.

**Tech Stack:** Python 3.13, Textual, Starlette/uvicorn (webterm only), systemd,
Caddy 2.6.2 on the VPS.

**Spec:** `docs/superpowers/specs/2026-09-07-retire-web-ui-tui-over-web-design.md`
(stage 6 of its *Sequencing*, and its *Deletion inventory*), as amended by
`docs/superpowers/specs/2026-09-13-aegis-web-as-a-client-design.md`.

## Global Constraints

- Work on `main`. This is a shared checkout: `git add` only explicit paths, commit with `git commit -m … -- <paths>`, never `git add -A`, never amend.
- Run Python through `/home/apiad/Workspace/repos/aegis/.venv/bin/python` or `uv run`. Never pip.
- The repo's gate is its own target: `make test` runs `uv run pytest -q -n auto -m "not slow" --max-unmarked-duration=3`. Run it verbatim; never through a pipe.
- **Known pre-existing failure.** `tests/test_workflow_registry_boot.py::test_resolve_boot_registers_a_builtin_named_in_workflows` fails under `-n auto` and passes alone. It failed at `14fea7c` with that commit's own source and is unrelated to this plan. The baseline to beat is **1 failed, 4726 passed, 45 skipped** at `c4aed3c`. One failure, that one, is green for this plan's purposes; two is a regression.
- `uv run ty check src/` reports **350** diagnostics at `c4aed3c`. No task may raise it.
- `rift check` reports **0 errors, 1 warning** at `c4aed3c`. It must still, at every commit. `rift`'s "every CLI command is documented" rule means deleting a command requires deleting its docs mention, and *keeping* one requires keeping it.
- **`src/aegis/remote/` is not `--remote`.** `remote/plane.py` and `remote/callback_observer.py` are the MCP remote plane (execution hosts, `/spawn main@vps`) and **stay**. Only `remote/ssh_tunnel.py` belongs to the dying flag. Likewise `docs/remote.md` documents the remote plane and stays; `know-how/remote-tui.md` documents the dying flag and goes.
- **Two test families share the `test_remote_` prefix and nothing else.** Delete only the ones this plan names. `test_remote_plane.py`, `test_remote_budget_*`, `test_remote_callback_*`, `test_remote_schedule_*`, `test_remote_mcp_target.py`, `test_remote_config.py`, `test_remote_client.py` and `tests/remote/` are the MCP plane and **stay**.
- Deleting a test is a loss of coverage unless the behaviour is covered elsewhere. Task 3 Step 1 is an audit, and its findings are the gate on what may be deleted.

## Review Focus

Five things the spec implies, that no task below would otherwise test, ordered by how badly they bite a person:

1. **A browser that installed the old PWA keeps its service worker.** It is registered at scope `/` on dev.apiad.net and intercepts every GET for that origin. Deleting `/service-worker.js` does not unregister it. A reasonable person opening dev.apiad.net on the phone they installed it on expects the new terminal, not a stale shell. Task 5 owns the tombstone and its test.
2. **`aegis web` under systemd must not autostart a daemon beside systemd's.** With two units, `aegis web` calling `ensure_daemon` races `aegis-server.service` and can leave a second daemon holding the root. Task 4 owns `--no-autostart` and its test.
3. **A `.aegis.yaml` with a token-bearing `web:` block must still boot a daemon.** Stage 5b made the daemon ignore the block; stage 6 deletes the code that used to read it. A config that worked yesterday must not raise. Task 3 owns that test.
4. **`aegis serve` must keep working after the units are rewritten.** The hidden alias exists for `aegis bench --target` and for units written before the rename. Task 6 must not remove it. Task 1 owns the test that it survives this plan.
5. **An old `.aegis/state/` holds `web.port` and stale view files.** Deleting the web layer must not make a daemon refuse to start against a state dir written by the previous version. Task 3 owns that test.

---

## File structure

| File | Fate |
|---|---|
| `src/aegis/web/` (21 files: `wssession.py` 495, `subscriptions.py` 423, `server.py` 165, `frontend.py` 63, `compact.py` 57, `history.py` 38, `__init__.py`, plus `static/` — `app.js` 1051, `base.css` 350, `renderEvent.js` 227, `ws.js` 144, `coalesce.js` 85, `markdown.js` 74, `service-worker.js` 51, `index.html` 48, `tabs.js` 34, `manifest.webmanifest` 17, `queues.js` 7, `icon.svg` 6, two PNGs) | **delete** (Task 3) |
| `src/aegis/tui/remote_manager.py` (421), `src/aegis/tui/ws_client.py` (232) | **delete** (Task 2) |
| `src/aegis/remote/ssh_tunnel.py` | **delete** (Task 1) — verify no other importer first |
| `src/aegis/cli.py` | `--remote`, `--token`, `--tail`, `_build_remote_manager`, `_ssh_fetch_token`, `_maybe_autolaunch_serve`, `_run_tui_with_manager` removed (Task 1) |
| `src/aegis/tui/app.py` | `_DisabledPlaneStub`, the `manager=` parameter, 19 `_remote_manager` branches collapsed (Task 2) |
| `src/aegis/tui/pane.py:652` | one `_remote_manager` branch collapsed (Task 2) |
| `src/aegis/webterm/app.py` | gains the tombstone `/service-worker.js` route (Task 5) |
| `src/aegis/webterm/server.py`, `cli.py` | `aegis web --no-autostart` (Task 4) |
| `scripts/aegis-serve.service` | replaced by `scripts/aegis-server.service` + `scripts/aegis-web.service` (Task 6) |
| `know-how/remote-tui.md` | **delete** (Task 7) |
| `know-how/deploying-web.md` | rewritten for two units and no basicauth (Task 7) |
| `README.md`, `DESIGN.md`, `AGENTS.md`, `docs/configuration.md`, `docs/hosts.md`, `docs/budget.md`, `CHANGELOG.md`, `TASKS.md` | edited (Task 7) |

Task order is dependency order. Tasks 1–3 are the deletion and each leaves the
suite green on its own. Tasks 4–5 add what the deployment needs. Task 6 is the
deployment. Task 7 writes it down.

---

### Task 1: Take `--remote` off the command line

`--remote` is the only consumer of `RemoteSessionManager`. Removing the flag
first means Task 2 de-branches code nothing can reach, which is the safer order.

**Files:**
- Modify: `src/aegis/cli.py` — the `remote`/`token`/`tail` options in `run()` (`cli.py:193-207`), the `if remote is not None:` block (`cli.py:213-246`), `_build_remote_manager` (`cli.py:508`), `_ssh_fetch_token` (`cli.py:531`), `_maybe_autolaunch_serve` (`cli.py:541`), `_run_tui_with_manager` (`cli.py:580`), the `TYPE_CHECKING` import at `cli.py:28`
- Delete: `src/aegis/remote/ssh_tunnel.py`
- Delete: `tests/cli/test_remote_flag.py`, `tests/test_remote_fork_unsupported.py`, `tests/live/test_remote_tui_live.py`
- Test: `tests/cli/test_remote_flag_is_gone.py` (new)

**Interfaces:**
- Consumes: nothing from a previous task.
- Produces: `aegis --help` carries no `--remote`, `--token` or `--tail`. `aegis server` and `aegis serve` are untouched.

- [ ] **Step 1: Verify `ssh_tunnel` has no other importer**

```bash
cd /home/apiad/Workspace/repos/aegis
grep -rn 'ssh_tunnel\|SSHTunnel' src/ tests/ --include=*.py | grep -v '^src/aegis/remote/ssh_tunnel.py' | grep -v '^src/aegis/cli.py'
```

Expected: no output. The SSH *execution hosts* feature (`/spawn main@vps`) uses
its own ControlMaster code, not this class. **If this prints anything, stop:**
`ssh_tunnel.py` has another owner, so leave the file and delete only the
`--remote` call site.

- [ ] **Step 2: Write the failing test**

`tests/cli/test_remote_flag_is_gone.py`:

```python
"""`aegis --remote` is gone, and the commands beside it are not.

The flag carried a second, semantic protocol between the UI and the brain.
It rotted — eighteen subsystems never got a field — and the daemon's byte
protocol replaced it. A reader finding `--remote` in a release note should
find nothing in the CLI.
"""
import re

from typer.testing import CliRunner


def test_the_remote_flags_are_gone():
    from aegis.cli import app

    out = CliRunner().invoke(app, ["--help"]).output
    for flag in ("--remote", "--token", "--tail"):
        assert flag not in out, f"{flag} is still offered"


def test_passing_remote_is_an_error_not_a_silent_no_op():
    from aegis.cli import app

    r = CliRunner().invoke(app, ["--remote", "ws://localhost:8080"])
    assert r.exit_code != 0, "aegis accepted a flag it no longer implements"


def test_the_daemon_commands_survive():
    """Review Focus 4: the hidden `serve` alias is what `aegis bench
    --target <old>` and any unit written before the rename still call."""
    from aegis.cli import app

    out = CliRunner().invoke(app, ["--help"]).output
    assert re.search(r"│ server\s", out), out
    assert re.search(r"│ web\s", out), out
    assert not re.search(r"│ serve\s", out), "serve should stay hidden, not listed"
    assert CliRunner().invoke(app, ["serve", "--help"]).exit_code == 0, (
        "the hidden serve alias stopped resolving"
    )
```

- [ ] **Step 3: Run it to verify it fails**

Run: `cd /home/apiad/Workspace/repos/aegis && .venv/bin/python -m pytest tests/cli/test_remote_flag_is_gone.py -q -p no:cacheprovider`
Expected: FAIL — `--remote is still offered`. `test_the_daemon_commands_survive` passes already; that is the point of including it, it must never go red.

- [ ] **Step 4: Remove the options from `run()`**

In `src/aegis/cli.py`, delete these three parameters from `run()`'s signature
(they sit between `clean` and the closing `) -> None:`):

```python
    remote: str = typer.Option(
        None,
        "--remote",
        help="Run against a remote aegis serve. "
        "ws://host:port or wss://host:port. "
        "Empty value = ws://localhost:8080.",
    ),
    token: str = typer.Option(
        None,
        "--token",
        help="Web token for --remote ws://. Required for ws:// remotes.",
    ),
    tail: int = typer.Option(
        10, "--tail", help="On subscribe/resume, replay last N coalesced blocks."
    ),
```

- [ ] **Step 5: Remove the dispatch block**

Delete the whole `if remote is not None:` block — from that line through the
`return` that ends it, i.e. everything between

```python
    if remote is not None:
```

and the line immediately before

```python
    root = find_project_root() or Path.cwd()
```

- [ ] **Step 6: Remove the four helpers and the import**

Delete these whole functions from `src/aegis/cli.py`: `_build_remote_manager`,
`_ssh_fetch_token`, `_maybe_autolaunch_serve`, `_run_tui_with_manager`. Delete
the `TYPE_CHECKING` import line:

```python
    from aegis.tui.remote_manager import RemoteSessionManager
```

Then delete the module and its tests:

```bash
cd /home/apiad/Workspace/repos/aegis
git rm -q src/aegis/remote/ssh_tunnel.py tests/cli/test_remote_flag.py \
  tests/test_remote_fork_unsupported.py tests/live/test_remote_tui_live.py
```

- [ ] **Step 7: Confirm nothing still references them**

```bash
cd /home/apiad/Workspace/repos/aegis
grep -nE '_build_remote_manager|_ssh_fetch_token|_maybe_autolaunch_serve|_run_tui_with_manager|ssh_tunnel' src/ tests/ -r --include=*.py
```

Expected: no output.

- [ ] **Step 8: Run the tests**

Run: `cd /home/apiad/Workspace/repos/aegis && .venv/bin/python -m pytest tests/cli tests/test_detach_and_quit.py -q -n auto -p no:cacheprovider`
Expected: PASS, including all three tests from Step 2.

- [ ] **Step 9: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
uv run ruff format src/aegis/cli.py tests/cli/test_remote_flag_is_gone.py
git add tests/cli/test_remote_flag_is_gone.py
git commit -m "refactor(cli): aegis --remote is gone; the daemon's socket replaced it" -- src/aegis/cli.py src/aegis/remote/ssh_tunnel.py tests/cli/test_remote_flag_is_gone.py tests/cli/test_remote_flag.py tests/test_remote_fork_unsupported.py tests/live/test_remote_tui_live.py
```

---

### Task 2: Collapse the twenty `_remote_manager` branches and delete the manager

Every `hasattr(self, "_remote_manager")` is now permanently False, because
nothing sets it. Each branch collapses onto its local side. Do them one at a
time and read each; two of them are not simple deletions.

**Files:**
- Modify: `src/aegis/tui/app.py` — `_DisabledPlaneStub` (`app.py:132`), the `manager is not None` branch in `__init__` (`app.py:532`), and the guards at `app.py:814, 1481, 1534, 1590, 1958, 2077, 2099, 2134, 2353, 2615, 3064`
- Modify: `src/aegis/tui/pane.py:652`
- Delete: `src/aegis/tui/remote_manager.py`, `src/aegis/tui/ws_client.py`
- Delete: `tests/tui/test_remote_manager.py`, `tests/tui/test_remote_manager_i1_i2.py`, `tests/tui/test_remote_manager_parity.py`, `tests/tui/test_remote_pane_hydration.py`, `tests/tui/test_remote_quit.py`, `tests/tui/test_remote_status_banner.py`, `tests/tui/test_ws_client_auth.py`, `tests/tui/test_ws_client_reconnect.py`, `tests/tui/test_ws_client_streams.py`
- Modify: `tests/tui/conftest.py` (drop `FakeWsClient`), `tests/test_hosts_commands.py`, `tests/test_recap_command.py`, `tests/tui/test_local_bridge_injection.py`
- Test: `tests/tui/test_no_remote_branches.py` (new)

**Interfaces:**
- Consumes: Task 1's removal of the only caller that passed `manager=`.
- Produces: `AegisApp.__init__` no longer accepts `manager=`. `aegis.tui.remote_manager` and `aegis.tui.ws_client` no longer import.

- [ ] **Step 1: Write the failing test**

`tests/tui/test_no_remote_branches.py`:

```python
"""The `--remote` mode left twenty branches in the TUI; none may survive it.

A dead `hasattr(self, "_remote_manager")` reads as a live mode to the next
person, and each one guards a real behaviour (spawn, quit, persistence, the
fleet dashboard) whose remote half no longer exists.
"""
import ast
import inspect
from pathlib import Path

import aegis.tui.app
import aegis.tui.pane

SOURCES = [Path(inspect.getfile(m)) for m in (aegis.tui.app, aegis.tui.pane)]


def test_no_module_named_remote_manager_or_ws_client():
    import importlib.util

    for name in ("aegis.tui.remote_manager", "aegis.tui.ws_client"):
        assert importlib.util.find_spec(name) is None, f"{name} still exists"


def test_no_remote_manager_branch_survives():
    for path in SOURCES:
        text = path.read_text()
        assert "_remote_manager" not in text, f"{path.name} still branches on it"
        assert "_DisabledPlaneStub" not in text, f"{path.name} still stubs planes"


def test_the_app_no_longer_takes_a_manager():
    sig = inspect.signature(aegis.tui.app.AegisApp.__init__)
    assert "manager" not in sig.parameters, (
        "`manager=` was the --remote seam; `bridge=` is the embedded one"
    )


def test_the_tui_module_still_parses_and_carries_its_bindings():
    """A de-branching that broke an `if` would still import; this asserts the
    class survived with its key map, which is what a user touches."""
    for path in SOURCES:
        ast.parse(path.read_text())
    keys = {b.key for b in aegis.tui.app.AegisApp.BINDINGS}
    assert {"ctrl+t", "ctrl+q"} <= keys, keys
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /home/apiad/Workspace/repos/aegis && .venv/bin/python -m pytest tests/tui/test_no_remote_branches.py -q -p no:cacheprovider`
Expected: FAIL on all of the first three; the fourth passes and must keep passing.

- [ ] **Step 3: Collapse `__init__`**

In `src/aegis/tui/app.py`, delete the `_DisabledPlaneStub` class (`app.py:132`,
through the end of its `__getattr__`). Then in `__init__`, delete the whole
`if manager is not None:` branch — the one that opens with the comment
`# --remote path: use the externally-built manager as the AppBridge.` and sets
`self._remote_manager = manager` — keeping the `else`/fallthrough body that
constructs the local planes. Remove `manager` from `__init__`'s signature and
from its docstring. Leave every mention of `bridge` alone: that is the embedded
seam and a different thing.

- [ ] **Step 4: Collapse the eleven guards in `app.py`**

Each of these becomes its local side. Work top-down so the line numbers below
stay usable, re-reading each site before editing:

| Site | What it does now | After |
|---|---|---|
| `app.py:814` | `if hasattr(...): self._wire_remote_handlers(); …; return`-ish | delete the whole branch **and** the `_wire_remote_handlers` method it calls |
| `app.py:1481` | `isinstance(pane, ConversationPane) and not hasattr(...)` | `isinstance(pane, ConversationPane)` |
| `app.py:1534` | `qm = None if hasattr(...) else self.queue_manager` | `qm = self.queue_manager` |
| `app.py:1590` | `if hasattr(...): return` inside the workspace persist | delete the two lines and the comment above them |
| `app.py:1958` | `if hasattr(...): await self._action_new_tab_remote(); return` | delete the branch **and** the `_action_new_tab_remote` method |
| `app.py:2077` | `if self._hosts and not hasattr(...)` | `if self._hosts` |
| `app.py:2099` | `if hasattr(...): self.notify("custom-model spawn isn't supported in remote mode yet…"); return` | delete the branch |
| `app.py:2134` | `if hasattr(...): …remote spawn… else: await self._spawn(choice, host=host)` | keep only `await self._spawn(choice, host=host)` |
| `app.py:2353` | `if hasattr(...): self.notify("The fleet dashboard shows local sessions only"); return` | delete the branch |
| `app.py:2615` | `if hasattr(...): await self._remote_manager.shutdown(); self.exit(); return` | delete the branch, leaving the `if not self._owns_brain:` that follows |
| `app.py:3064` | `core = self._remote_manager.make_pane_core(info.handle)` | this line is inside a method only the remote path called — delete the whole method and confirm with `grep -n '<method name>' src/aegis/tui/app.py` that nothing calls it |

Then `src/aegis/tui/pane.py:652`: read the guard and collapse it onto the
branch taken when `self.app` has no `_remote_manager`.

- [ ] **Step 5: Delete the modules and their tests**

```bash
cd /home/apiad/Workspace/repos/aegis
git rm -q src/aegis/tui/remote_manager.py src/aegis/tui/ws_client.py \
  tests/tui/test_remote_manager.py tests/tui/test_remote_manager_i1_i2.py \
  tests/tui/test_remote_manager_parity.py tests/tui/test_remote_pane_hydration.py \
  tests/tui/test_remote_quit.py tests/tui/test_remote_status_banner.py \
  tests/tui/test_ws_client_auth.py tests/tui/test_ws_client_reconnect.py \
  tests/tui/test_ws_client_streams.py
```

- [ ] **Step 6: Narrow the three parity tests rather than deleting them**

These assert that `RemoteSessionManager` mirrors `SessionManager` and
`AegisApp`. **The mirror is gone; the thing it mirrored is not.** Drop only the
`RemoteSessionManager` axis from each, keeping every other assertion:

- `tests/test_hosts_commands.py` — the import at `:340`, the `("RemoteSessionManager", RemoteSessionManager.spawn)` tuple at `:347`, and the `RemoteSessionManager, RemoteUnsupportedError,` reference at `:455`. Keep the `SessionManager` and `AegisApp` entries and the docstring's point.
- `tests/test_recap_command.py` — the import at `:63` and `RemoteSessionManager` in the `for impl in (...)` tuple at `:66`.
- `tests/tui/test_local_bridge_injection.py` — the import at `:177`, the `hasattr(RemoteSessionManager, name)` loop at `:184`, and rewrite the docstring at `:190` (`"three exist only on RemoteSessionManager"`) to say what the remaining assertions cover.

In `tests/tui/conftest.py`, delete the `FakeWsClient` class and the
`fake_ws_client` fixture, then check no test still asks for the fixture:

```bash
cd /home/apiad/Workspace/repos/aegis
grep -rn 'fake_ws_client\|FakeWsClient' tests/
```

Expected: no output.

- [ ] **Step 7: Run the TUI suites**

Run: `cd /home/apiad/Workspace/repos/aegis && .venv/bin/python -m pytest tests/tui tests/test_hosts_commands.py tests/test_recap_command.py tests/test_detach_and_quit.py -q -n auto -p no:cacheprovider`
Expected: PASS. A failure here is a branch collapsed onto the wrong side — read the test, not the diff.

- [ ] **Step 8: Prove the TUI still runs, not just that it imports**

A green unit suite does not mean the app mounts. Drive the real one:

```bash
cd /home/apiad/Workspace/repos/aegis
R=$(mktemp -d /tmp/aegis-t2-XXXX)
printf 'default_agent: main\nagents:\n  main:\n    provider: claude-code\n    model: sonnet\n' > "$R/.aegis.yaml"
AEGIS_DAEMON_DIR="$R/.daemons" timeout 60 .venv/bin/python -m aegis web --no-browser --cwd "$R" > "$R/web.log" 2>&1 &
sleep 8
.venv/bin/python - "$R" <<'PY'
import asyncio, re, sys, time
from pathlib import Path
from websockets.asyncio.client import connect
from aegis.daemon.protocol import FrameDecoder, hello
log = Path(sys.argv[1], "web.log").read_text()
m = re.search(r"http://127\.0\.0\.1:(\d+)/\?t=(\S+)", log)
assert m, log
port, token = m.group(1), m.group(2)
async def main():
    screen, dec = bytearray(), FrameDecoder()
    async with connect(f"ws://127.0.0.1:{port}/term",
                       additional_headers={"Cookie": f"aegis_web={token}"}) as ws:
        await ws.send(hello("t2-check", 100, 30))
        end = time.monotonic() + 45
        while b"type a message" not in screen and time.monotonic() < end:
            for k, p in dec.feed(await ws.recv()):
                if k == "D": screen.extend(p)
    ok = b"type a message" in screen
    print("the TUI mounted and drew its input:", ok)
    sys.exit(0 if ok else 1)
asyncio.run(main())
PY
echo "rc=$?"
```

Expected: `the TUI mounted and drew its input: True` and `rc=0`. Stop the
`aegis web` and daemon processes by PID afterwards. **Read the rc directly as
printed — do not pipe this into anything.**

- [ ] **Step 9: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
uv run ruff format src/aegis/tui/app.py src/aegis/tui/pane.py tests/tui/
uv run ruff check src/
git add tests/tui/test_no_remote_branches.py
git commit -m "refactor(tui): collapse the --remote branches and delete RemoteSessionManager" -- src/aegis/tui/app.py src/aegis/tui/pane.py src/aegis/tui/remote_manager.py src/aegis/tui/ws_client.py tests/tui tests/test_hosts_commands.py tests/test_recap_command.py
```

---

### Task 3: Delete `src/aegis/web/`, keeping the coverage it happened to hold

Some `test_web_*.py` files test aegis behaviour *through* the web session
rather than testing the web. Deleting those silently drops the only test of a
live feature. Step 1 is the audit that decides.

**Files:**
- Delete: `src/aegis/web/` (whole tree)
- Delete: the web tests Step 1 clears for deletion
- Modify / port: whatever Step 1 does not clear
- Test: `tests/test_no_old_web_layer.py` (new)

**Interfaces:**
- Consumes: Task 2's removal of `ws_client`, the other speaker of this protocol.
- Produces: `aegis.web` no longer imports.

- [ ] **Step 1: Audit every web test before deleting one**

For each file below, open it, name the behaviour it asserts, and search for
another test covering that same behaviour. Record the verdict in this plan as a
table before deleting anything.

```bash
cd /home/apiad/Workspace/repos/aegis
ls tests/test_web_*.py tests/test_wssession_*.py tests/test_comms_web_wire.py
```

The eighteen `test_web_*`, two `test_wssession_*` and `test_comms_web_wire.py`
split three ways:

- **Plumbing — delete.** Tests of the WS protocol, routes, static files, PWA, compact encoding and session list: `test_web_protocol.py`, `test_web_server.py`, `test_web_static_routes.py`, `test_web_pwa.py`, `test_web_compact.py`, `test_web_history.py`, `test_web_subscriptions.py`, `test_web_session_list.py`, `test_web_slash.py` and `test_web_complete.py` (both docstrings say "Web parity", and the second imports the first), `test_wssession_handoff_rename.py`, `test_wssession_tail.py`.
- **Named `test_web_*` but not about the old web — KEEP.** `tests/test_web_cli.py` tests `aegis.cli._ensure_web_token`, which `aegis web` and `aegis token` both still call; its two tests are the only coverage of token generation and idempotency. Do not delete it. Rename it to `tests/test_web_token.py` so the next reader is not tempted, and say so in the commit body.
- **Check for a sibling first.** `test_web_config_edit.py` (4 tests), `test_web_config_show.py` (2), `test_web_files.py` (8), `test_web_group_status.py` (2), `test_web_queue_digest.py` (4), `test_web_download.py` (4). Each drives a real aegis feature through `SubscriptionRegistry`. For each, run the grep that would find its sibling, e.g. for config edit:
  ```bash
  grep -rn 'config_add_agent' tests/ --include=*.py | grep -v test_web_
  ```
  A hit in `tests/test_mcp_config_tools.py` means the behaviour is covered and the web test is plumbing. **A miss means port the assertions to a non-web test before deleting the file**, and say so in the commit message.
- **Port the premise.** `tests/test_comms_web_wire.py` imports `aegis.web.compact.compact_encoded` and reads `web/static/js/renderEvent.js` to assert one glyph table across the TUI and the browser. The browser half is gone, but the glyph table is not. Fold whatever it asserts about the table itself into `tests/test_comms_render.py` or `tests/test_render_event.py`, then delete it.

**Step 1 audit result (recorded 2026-09-27, before any deletion).**

The plan's three-way split covers 17 of the 18 `test_web_*.py` files. The
missing one is `tests/test_web_config.py`, added to the "keep" class below.

| File | Tests | Behaviour it asserts | Sibling coverage | Verdict |
|---|---|---|---|---|
| `test_web_protocol.py`, `test_web_server.py`, `test_web_static_routes.py`, `test_web_pwa.py`, `test_web_compact.py`, `test_web_history.py`, `test_web_subscriptions.py`, `test_web_session_list.py`, `test_web_slash.py`, `test_web_complete.py`, `test_wssession_handoff_rename.py`, `test_wssession_tail.py` | — | the WS protocol, routes, static files, PWA, compact encoding, session list | n/a — these test the deleted layer itself | **delete** |
| `test_web_cli.py` | 2 | `aegis.cli._ensure_web_token` — token generation and idempotency | none; it *is* the only coverage | **keep**, renamed `test_web_token.py` |
| `test_web_config.py` | 4 | `WebConfig` + `load_config`'s `web:` block parsing | none; it is the only coverage | **keep unchanged** — `WebConfig` lives in `src/aegis/config/` and `cli.py:1021,1030,1127` still reads it for the `aegis web` token. Named `test_web_*`, not about the old web. |
| `test_web_config_edit.py` | 4 | `config_add_agent` etc. through `SubscriptionRegistry` | `tests/test_mcp_config_tools.py` | **delete** |
| `test_web_config_show.py` | 2 | config_show listing agents/queues/schedules | `test_mcp_config_tools.py`, `test_comms_descriptors.py`, `test_mcp_server.py` | **delete** |
| `test_web_files.py` | 8 | file search/read + traversal guard via the web RPC | `FileIndexer` itself is covered by `test_file_index.py`, `test_file_picker.py`, `test_file_browser_tab.py`, `test_file_index_perf.py`. The traversal guard lives at `web/subscriptions.py:133` — inside the deleted package, guarding a browser-file-read RPC that webterm does not have. The attack surface is **removed, not left uncovered**. `test_file_search` only asserted the registry delegating to a pre-stuffed `_paths`. | **delete** |
| `test_web_group_status.py` | 2 | group status | `test_mcp_server.py`, `test_groups_mcp_maintenance.py` | **delete** |
| `test_web_queue_digest.py` | 4 | queue digest | `tests/test_queue_digest.py` (dedicated) | **delete** |
| `test_web_download.py` | 4 | `/download` route: 401, traversal 403, 404 | none — and none needed: `grep -rn download src/aegis/webterm/` is empty, so the route is deleted with the server rather than left untested | **delete** |
| `test_comms_web_wire.py` | 4 | one glyph table across TUI and browser | Two tests assert `aegis.web.compact.compact_encoded`, deleted. Two assert `renderEvent.js`, deleted. What survives — the table — is already asserted with the *same inputs*: `test_render_shared.py:85` (`KIND_ICON["read"] == "📖"`), `test_comms_render.py:64` (`render_tool_use` on `Read`/`kind=read` → `📖`), `test_comms_descriptors.py:33,35` (`aegis_glyph("aegis_handoff") == "⇄"` and the desc format). | **delete; the fold is a no-op** |

Nothing required porting. The one assertion unique to `test_comms_web_wire.py`
— that `compact_encoded` strips `raw_input` and sets a `comms` boolean on the
wire frame — describes a function this task deletes.

- [ ] **Step 2: Write the failing test**

`tests/test_no_old_web_layer.py`:

```python
"""The aegis-aware web layer is gone, and the daemon is fine without it.

It reached into the brain by attribute — eleven manager methods — and grew a
message per feature until it fell behind the TUI. `aegis web` replaced it
with a relay that knows no aegis concept.
"""
import importlib.util
from pathlib import Path

import aegis


def test_the_web_package_is_gone():
    assert importlib.util.find_spec("aegis.web") is None
    assert not (Path(aegis.__file__).parent / "web").exists()


def test_a_config_with_a_web_block_still_boots_a_brain(tmp_path):
    """Review Focus 3: the daemon stopped reading the block in stage 5b, and
    a config written before that must not raise now the code is deleted."""
    from aegis.cli import load_boot_config
    from aegis.config.roots import AegisRoots

    (tmp_path / ".aegis.yaml").write_text(
        "default_agent: main\nagents:\n  main:\n    provider: claude-code\n"
        "    model: opus\nweb:\n  bind: 127.0.0.1\n  port: 8899\n  token: secret\n",
        encoding="utf-8")
    boot = load_boot_config(AegisRoots.for_project(tmp_path))
    assert boot.default_agent == "main"
    assert not hasattr(boot, "web")


def test_a_state_dir_from_the_old_version_is_not_rejected(tmp_path):
    """Review Focus 5: an upgraded install has `web.port` and old view files
    lying in .aegis/state. Reading the config must not care."""
    from aegis.cli import load_boot_config
    from aegis.config.roots import AegisRoots
    from aegis.state.workspace import state_dir

    (tmp_path / ".aegis.yaml").write_text(
        "default_agent: main\nagents:\n  main:\n    provider: claude-code\n"
        "    model: opus\n", encoding="utf-8")
    sd = state_dir(tmp_path)
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "web.port").write_text("8899")
    (sd / "views").mkdir(exist_ok=True)
    (sd / "views" / "legacy.json").write_text("{}")
    assert load_boot_config(AegisRoots.for_project(tmp_path)).default_agent == "main"
```

- [ ] **Step 3: Run it to verify it fails**

Run: `cd /home/apiad/Workspace/repos/aegis && .venv/bin/python -m pytest tests/test_no_old_web_layer.py -q -p no:cacheprovider`
Expected: FAIL on `test_the_web_package_is_gone`; the other two pass and must keep passing.

- [ ] **Step 4: Delete**

```bash
cd /home/apiad/Workspace/repos/aegis
git rm -rq src/aegis/web
# `src/aegis/web/__pycache__` is gitignored, so `git rm -r` leaves the
# directory sitting on disk and `test_the_web_package_is_gone`'s .exists()
# check fails on a deletion that was actually correct.
rm -rf src/aegis/web
git mv tests/test_web_cli.py tests/test_web_token.py
git rm -q tests/test_web_protocol.py tests/test_web_server.py \
  tests/test_web_static_routes.py tests/test_web_pwa.py tests/test_web_complete.py \
  tests/test_web_compact.py tests/test_web_history.py tests/test_web_subscriptions.py \
  tests/test_web_session_list.py tests/test_web_slash.py tests/test_web_config_edit.py \
  tests/test_web_config_show.py tests/test_web_files.py tests/test_web_group_status.py \
  tests/test_web_queue_digest.py tests/test_web_download.py \
  tests/test_wssession_handoff_rename.py tests/test_wssession_tail.py \
  tests/test_comms_web_wire.py
```

`tests/test_web_token.py` imports only `aegis.cli._ensure_web_token`, so it
needs no edit beyond the rename.

Then check nothing references the package:

```bash
grep -rn 'aegis\.web\b\|from aegis import web\|WebFrontend\|WSSession\|SubscriptionRegistry' src/ tests/ --include=*.py
```

Expected: no output. `src/aegis/webterm/` is a different package and must not appear.

- [ ] **Step 5: Run the whole gate**

Run: `cd /home/apiad/Workspace/repos/aegis && make test`
Expected: the Global Constraints baseline — 1 failed (the known pre-existing
workflow-registry test), nothing else. The passing count drops by roughly the
142 tests these files held; that is the deletion, not a regression. **Any
second failure means Step 1's audit missed a behaviour: port it, do not delete
the assertion.**

- [ ] **Step 6: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
git add tests/test_no_old_web_layer.py
git commit -m "refactor(web): delete the aegis-aware web layer and its client" -- src/aegis/web tests/test_no_old_web_layer.py tests/test_web_token.py tests/test_web_protocol.py tests/test_web_server.py tests/test_web_static_routes.py tests/test_web_pwa.py tests/test_web_complete.py tests/test_web_compact.py tests/test_web_history.py tests/test_web_subscriptions.py tests/test_web_session_list.py tests/test_web_slash.py tests/test_web_config_edit.py tests/test_web_config_show.py tests/test_web_files.py tests/test_web_group_status.py tests/test_web_queue_digest.py tests/test_web_download.py tests/test_wssession_handoff_rename.py tests/test_wssession_tail.py tests/test_comms_web_wire.py
```

(Add any file Step 1 made you port into, and name the ports in the commit body.)

---

### Task 4: `aegis web --no-autostart`, so two units do not race

**Files:**
- Modify: `src/aegis/webterm/server.py` (`connect_for`), `src/aegis/cli.py` (the `web` command)
- Test: `tests/webterm/test_no_autostart.py` (new)

**Interfaces:**
- Consumes: `aegis.webterm.server.connect_for(root, *, preflight=None) -> Connect`; `aegis.daemon.lifecycle.socket_path(roots) -> Path`.
- Produces: `connect_for(root, *, preflight=None, autostart: bool = True) -> Connect` and `aegis web --no-autostart`.

- [ ] **Step 1: Write the failing test**

`tests/webterm/test_no_autostart.py`:

```python
"""Under systemd, `aegis web` must not start a daemon of its own.

Review Focus 2: `aegis-server.service` owns the daemon. A web process that
also calls `ensure_daemon` races it on boot and can leave a second daemon
holding the root, which is the failure the whole daemon programme exists to
remove.
"""
from __future__ import annotations

import asyncio

import pytest

from aegis.config.roots import AegisRoots
from aegis.daemon.lifecycle import socket_path
from aegis.webterm.server import connect_for


async def test_no_autostart_refuses_instead_of_spawning(tmp_path, monkeypatch):
    spawned = []

    async def _never(*a, **kw):
        spawned.append(1)
        raise AssertionError("ensure_daemon was called with autostart off")

    monkeypatch.setattr("aegis.webterm.server.ensure_daemon", _never)
    connect = connect_for(tmp_path, autostart=False)
    with pytest.raises(ConnectionError) as e:
        await connect()
    assert spawned == []
    assert str(socket_path(AegisRoots.for_project(tmp_path))) in str(e.value), (
        "the refusal must name the socket it looked for"
    )


async def test_no_autostart_still_connects_to_a_daemon_that_is_there(tmp_path):
    sock = socket_path(AegisRoots.for_project(tmp_path))
    sock.parent.mkdir(parents=True, exist_ok=True)
    server = await asyncio.start_unix_server(
        lambda r, w: w.close(), path=str(sock))
    try:
        reader, writer = await connect_for(tmp_path, autostart=False)()
        writer.close()
    finally:
        server.close()


async def test_autostart_is_still_the_default(tmp_path, monkeypatch):
    """A laptop `aegis web` with nothing running must still work."""
    called = []

    async def _ensure(root, **kw):
        called.append(root)
        raise RuntimeError("stop here; the call is the assertion")

    monkeypatch.setattr("aegis.webterm.server.ensure_daemon", _ensure)
    with pytest.raises(RuntimeError):
        await connect_for(tmp_path)()
    assert called == [tmp_path]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /home/apiad/Workspace/repos/aegis && .venv/bin/python -m pytest tests/webterm/test_no_autostart.py -q -p no:cacheprovider`
Expected: FAIL — `connect_for() got an unexpected keyword argument 'autostart'`.

- [ ] **Step 3: Implement**

In `src/aegis/webterm/server.py`, replace `connect_for` with:

```python
def connect_for(root: Path, *, preflight=None, autostart: bool = True) -> Connect:
    """Each connection finds (or starts) the daemon the way a terminal does,
    so a restarted daemon is found again and a stopped one is started.

    With ``autostart=False`` it only ever connects. systemd owns the daemon
    under `aegis-server.service`, and a web process that spawned its own
    would race that unit for the root's lock on boot.
    """

    async def connect():
        if autostart:
            path = await ensure_daemon(Path(root), preflight=preflight)
        else:
            path = socket_path(AegisRoots.for_project(Path(root)))
            if not path.exists():
                raise ConnectionError(
                    f"no daemon socket at {path} and --no-autostart is set; "
                    "start aegis-server.service first"
                )
        return await asyncio.open_unix_connection(str(path))

    return connect
```

and add to its imports:

```python
from aegis.config.roots import AegisRoots
from aegis.daemon.lifecycle import ensure_daemon, socket_path
```

(replacing the existing `from aegis.daemon.lifecycle import ensure_daemon`).

In `src/aegis/cli.py`'s `web` command, add the option after `no_browser`:

```python
    no_autostart: bool = typer.Option(
        False,
        "--no-autostart",
        help="Never start a daemon; connect to one that is already running. "
        "For systemd, where aegis-server.service owns the daemon.",
    ),
```

pass it through to the app:

```python
    app_ = build_webterm_app(
        token=token,
        connect=_webserver.connect_for(
            root,
            preflight=lambda: _daemon_preflight(root),
            autostart=not no_autostart,
        ),
    )
```

and guard the up-front `ensure_daemon` inside `_main`:

```python
    async def _main():
        # Up front, so a daemon that cannot start is reported here rather
        # than as a browser that never draws. With --no-autostart there is
        # nothing to start: systemd's unit is already up, or it is not, and
        # the first browser gets a refusal naming the socket.
        if not no_autostart:
            await _ensure_daemon(root, preflight=lambda: _daemon_preflight(root))
```

- [ ] **Step 4: Run the tests**

Run: `cd /home/apiad/Workspace/repos/aegis && .venv/bin/python -m pytest tests/webterm tests/cli/test_web_command.py -q -p no:cacheprovider`
Expected: PASS, including the three new tests.

- [ ] **Step 5: Prove it on the real command**

```bash
cd /home/apiad/Workspace/repos/aegis
R=$(mktemp -d /tmp/aegis-t4-XXXX)
printf 'default_agent: main\nagents:\n  main:\n    provider: claude-code\n    model: sonnet\nweb:\n  port: 8979\n  token: t4\n' > "$R/.aegis.yaml"
AEGIS_DAEMON_DIR="$R/.daemons" timeout 40 .venv/bin/python -m aegis web --no-browser --no-autostart --cwd "$R" > "$R/web.log" 2>&1 &
sleep 6
curl -s -o /dev/null -w 'healthz %{http_code}\n' http://127.0.0.1:8979/healthz
ls "$R/.aegis/state/daemon.sock" 2>&1
pgrep -af "aegis server --cwd $R" | grep -v bash
```

Expected: `healthz 200` (the web process serves), **no** `daemon.sock`, and
**no** daemon process — `aegis web` came up without spawning one. Stop it by
PID afterwards.

- [ ] **Step 6: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
uv run ruff format src/aegis/webterm/server.py src/aegis/cli.py tests/webterm/test_no_autostart.py
git add tests/webterm/test_no_autostart.py
git commit -m "feat(web): --no-autostart, so systemd owns the daemon" -- src/aegis/webterm/server.py src/aegis/cli.py tests/webterm/test_no_autostart.py
```

---

### Task 5: A tombstone service worker, so installed PWAs do not haunt the new page

**Files:**
- Create: `src/aegis/webterm/static/service-worker.js`
- Modify: `src/aegis/webterm/app.py` (one route)
- Test: `tests/webterm/test_tombstone_sw.py` (new)

**Interfaces:**
- Consumes: `build_webterm_app(*, token, connect, static_dir=None) -> Starlette`.
- Produces: `GET /service-worker.js` returns an unregistering worker, with no token required.

- [ ] **Step 1: Write the failing test**

`tests/webterm/test_tombstone_sw.py`:

```python
"""Review Focus 1: the old PWA's service worker outlives its server.

It was registered at scope `/` on dev.apiad.net and intercepts every GET for
that origin. Deleting the old client does not unregister it — the browser
only drops a worker whose script it can no longer fetch, and only on an
update check. Serving a worker that unregisters itself is how you kill one.
"""
from __future__ import annotations

from starlette.testclient import TestClient

from aegis.webterm.app import build_webterm_app


def _client():
    async def connect():
        raise AssertionError("not reached")

    return TestClient(build_webterm_app(token="t", connect=connect),
                      base_url="https://testserver")


def test_the_worker_is_served_without_a_cookie():
    """An old worker's update check carries no cookie of ours. A 401 here
    leaves it installed and intercepting for ever."""
    r = _client().get("/service-worker.js")
    assert r.status_code == 200, r.status_code
    assert "javascript" in r.headers["content-type"]


def test_the_worker_unregisters_itself_and_drops_its_caches():
    body = _client().get("/service-worker.js").text
    assert "self.registration.unregister()" in body
    assert "caches.keys()" in body and "caches.delete" in body
    assert "skipWaiting" in body
    assert "addEventListener(\"fetch\"" not in body, (
        "a tombstone must not intercept fetches; that is what it is removing"
    )


def test_it_is_not_cached_by_the_browser():
    r = _client().get("/service-worker.js")
    assert "no-cache" in r.headers.get("cache-control", "").lower(), r.headers
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /home/apiad/Workspace/repos/aegis && .venv/bin/python -m pytest tests/webterm/test_tombstone_sw.py -q -p no:cacheprovider`
Expected: FAIL — 404 on `/service-worker.js`.

- [ ] **Step 3: Write the worker**

`src/aegis/webterm/static/service-worker.js`:

```js
// A tombstone, not a service worker.
//
// The retired aegis PWA registered a worker at scope "/" on this origin, and
// it stays registered in every browser that ever loaded that page — deleting
// the old client does not remove it. A registered worker intercepts every GET
// for the origin, so it would sit in front of the new terminal.
//
// A browser fetches this script on its next update check. Unregistering here
// is what actually removes it; the caches go with it so the old shell cannot
// be served from disk in the meantime. There is deliberately no fetch
// handler: this worker's whole job is to stop existing.

self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.map((k) => caches.delete(k)));
    await self.registration.unregister();
    for (const client of await self.clients.matchAll({ type: "window" })) {
      client.navigate(client.url);
    }
  })());
});
```

- [ ] **Step 4: Serve it**

In `src/aegis/webterm/app.py`, add the route handler inside
`build_webterm_app`, beside `healthz`:

```python
    async def service_worker(request):
        # No cookie check: an old worker's update check carries no cookie of
        # ours, and a 401 would leave it installed and intercepting for ever.
        return FileResponse(
            static / "service-worker.js",
            media_type="text/javascript",
            headers={"Cache-Control": "no-cache"},
        )
```

and register it in the `routes=[...]` list, before the `/static` mount:

```python
            Route("/service-worker.js", service_worker),
```

- [ ] **Step 5: Run the tests**

Run: `cd /home/apiad/Workspace/repos/aegis && .venv/bin/python -m pytest tests/webterm -m "not slow" -q -p no:cacheprovider`
Expected: PASS. `test_every_static_path_the_page_names_exists` still passes —
the page does not reference the worker, and nothing registers it; it exists to
be fetched by a worker that already exists.

- [ ] **Step 6: Mutation-check it**

```bash
cd /home/apiad/Workspace/repos/aegis
f=src/aegis/webterm/static/service-worker.js; cp $f /tmp/sw.bak
sed -i 's/await self.registration.unregister();//' $f
cmp -s $f /tmp/sw.bak && echo "MUTATION DID NOT APPLY"
.venv/bin/python -m pytest tests/webterm/test_tombstone_sw.py -q -p no:cacheprovider
cp /tmp/sw.bak $f
```

Expected: `test_the_worker_unregisters_itself_and_drops_its_caches` FAILS, then restored.

- [ ] **Step 7: Commit**

```bash
cd /home/apiad/Workspace/repos/aegis
uv run ruff format src/aegis/webterm/app.py tests/webterm/test_tombstone_sw.py
git add src/aegis/webterm/static/service-worker.js tests/webterm/test_tombstone_sw.py
git commit -m "fix(webterm): serve a tombstone worker so the retired PWA unregisters" -- src/aegis/webterm/static/service-worker.js src/aegis/webterm/app.py tests/webterm/test_tombstone_sw.py
```

---

### Task 6: Deploy the VPS as two units, and drop Caddy's basic auth

> **STOP AND ASK before starting this task.** It changes a public host
> (`dev.apiad.net`) and removes an authentication layer from it. Everything
> above is local and revertible; this is neither. Alex has said dev.apiad.net
> does not need to keep working during the change, so downtime is acceptable —
> but the change itself needs his go-ahead at the time.

**Files:**
- Create: `scripts/aegis-server.service`, `scripts/aegis-web.service`
- Delete: `scripts/aegis-serve.service`
- On the VPS: `/etc/systemd/system/aegis-*.service`, `/etc/caddy/Caddyfile`

**Interfaces:**
- Consumes: Task 4's `--no-autostart`; Task 5's tombstone.
- Produces: two units, and `dev.apiad.net` served by `aegis web`.

- [ ] **Step 1: Write the unit files**

`scripts/aegis-server.service`:

```ini
[Unit]
Description=aegis daemon (brain, views, MCP plane) on a unix socket
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/Workspace
ExecStart=/usr/bin/env uv run --directory %h/Workspace/repos/aegis aegis server
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
# The daemon reaps itself after 30 idle minutes, which is right for a laptop
# and wrong for a unit that is meant to stay up.
Environment=AEGIS_IDLE_TIMEOUT=0

[Install]
WantedBy=default.target
```

`scripts/aegis-web.service`:

```ini
[Unit]
Description=aegis web (browsers as clients of the daemon's socket)
After=network-online.target aegis-server.service
# Wants, NOT Requires: with Requires, `systemctl restart aegis-server`
# restarts this unit too and drops every browser — the opposite of the
# reconnect the relay exists to provide.
Wants=network-online.target aegis-server.service

[Service]
Type=simple
WorkingDirectory=%h/Workspace
ExecStart=/usr/bin/env uv run --directory %h/Workspace/repos/aegis aegis web --no-browser --no-autostart
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=/etc/aegis-web.env

[Install]
WantedBy=default.target
```

```bash
cd /home/apiad/Workspace/repos/aegis
git rm -q scripts/aegis-serve.service
```

- [ ] **Step 2: Commit and push, because the VPS deploys from origin**

```bash
cd /home/apiad/Workspace/repos/aegis
git add scripts/aegis-server.service scripts/aegis-web.service
git commit -m "chore(deploy): two units — the daemon and the web process" -- scripts/aegis-server.service scripts/aegis-web.service scripts/aegis-serve.service
git push origin main
```

Pushing first is a hard precondition: the VPS pulls from GitHub, so an
unpushed commit deploys nothing and reports success.

- [ ] **Step 3: Pull on the VPS and install the units**

```bash
ssh vps 'cd ~/Workspace/repos/aegis && git pull --ff-only origin main && git log --oneline -1'
ssh vps 'sudo systemctl stop aegis-web && sudo systemctl disable aegis-web'
ssh vps 'sudo cp ~/Workspace/repos/aegis/scripts/aegis-server.service /etc/systemd/system/aegis-server.service'
ssh vps 'sudo cp ~/Workspace/repos/aegis/scripts/aegis-web.service /etc/systemd/system/aegis-web.service'
ssh vps 'sudo sed -i "s|%h|/home/apiad|g" /etc/systemd/system/aegis-server.service /etc/systemd/system/aegis-web.service'
ssh vps 'sudo systemctl daemon-reload && sudo systemctl enable --now aegis-server && sleep 5 && sudo systemctl enable --now aegis-web'
ssh vps 'systemctl is-active aegis-server aegis-web'
```

Expected: `active` twice. If either is not, read
`ssh vps 'journalctl -u aegis-server -u aegis-web -n 40 --no-pager'` before
changing anything.

- [ ] **Step 4: Verify the daemon binds no port, from the VPS**

```bash
ssh vps 'ss -lntp | grep -E ":8899|aegis" || echo "no aegis TCP listener besides the one below"'
ssh vps 'sudo ss -lntp | grep 8899'
```

Expected: exactly one listener on 8899, and its process is the **`aegis web`**
one, not `aegis server`. Confirm by PID against
`ssh vps 'systemctl show -p MainPID aegis-web aegis-server'`.

- [ ] **Step 5: Drop Caddy's basic auth — the one dangerous edit**

The cookie exchange from stage 5b is already deployed by Step 3, so the token
is guarding the door before basic auth leaves it. Back up, edit, reload:

```bash
ssh vps 'sudo cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.pre-stage6'
ssh vps 'sudo grep -n -A6 "dev.apiad.net" /etc/caddy/Caddyfile'
```

Remove only the `basicauth` block from the `dev.apiad.net` site, leaving
`reverse_proxy 127.0.0.1:8899`. Then:

```bash
ssh vps 'sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile'
ssh vps 'sudo systemctl reload caddy && systemctl is-active caddy'
```

Expected: `valid configuration`, then `active`. If validate fails, restore
`/etc/caddy/Caddyfile.pre-stage6` and stop.

- [ ] **Step 6: Verify the door from outside, as a user reaches it**

```bash
curl -s -o /dev/null -w 'no auth: %{http_code}\n' https://dev.apiad.net/
curl -s -o /dev/null -w 'healthz: %{http_code}\n' https://dev.apiad.net/healthz
TOKEN=$(ssh vps 'sudo grep -o "AEGIS_WEB_TOKEN=.*" /etc/aegis-web.env | cut -d= -f2-')
curl -s -o /dev/null -w 'login: %{http_code}\n' -D- "https://dev.apiad.net/?t=$TOKEN" | head -1
```

Expected: `no auth: 401` (the token refuses, not Caddy), `healthz: 200`,
`login: 303`. **A 200 for the no-auth case means the door is open — restore
`Caddyfile.pre-stage6` and reload Caddy immediately.**

- [ ] **Step 7: Verify it in a browser, because that is how it is used**

Use the `saidkick` skill. Open `https://dev.apiad.net/?t=<token>` with
`--activate`, then record here, verbatim:

1. The address bar shows `https://dev.apiad.net/` with no `?t=`.
2. A screenshot shows the aegis tab bar and the `type a message…` input.
3. `ssh vps 'sudo systemctl restart aegis-server'`, then the page returns to its view within ~10s and `systemctl is-active aegis-web` is still `active` — the web process survived the daemon restart, which is what `Wants=` rather than `Requires=` buys.

- [ ] **Step 8: Commit the recorded results**

```bash
cd /home/apiad/Workspace/repos/aegis
git commit -m "docs(plan): record the stage 6 VPS deployment" -- docs/superpowers/plans/2026-09-27-aegis-stage-6-delete-the-old-web.md
git push origin main
```

---

### Task 7: Write it down

**Files:**
- Delete: `know-how/remote-tui.md`
- Modify: `know-how/deploying-web.md`, `README.md`, `DESIGN.md`, `AGENTS.md`, `docs/configuration.md`, `docs/hosts.md`, `docs/budget.md`, `CHANGELOG.md`, `TASKS.md`
- Modify: the status headers of both specs and of this plan

- [ ] **Step 1: Find every surviving mention**

```bash
cd /home/apiad/Workspace/repos/aegis
grep -rn -- '--remote' README.md AGENTS.md DESIGN.md TASKS.md docs/*.md know-how/*.md
grep -rn 'remote-tui\|PWA\|web client\|basicauth\|basic auth\|aegis-serve.service' README.md AGENTS.md DESIGN.md docs/*.md know-how/*.md
```

Rewrite each hit. `docs/remote.md` is the **MCP remote plane** and its `--remote`
hits (if any) are about a different feature — read before editing. Delete
`know-how/remote-tui.md` with `git rm`.

- [ ] **Step 2: Rewrite `know-how/deploying-web.md`**

Replace its frontmatter `when:` with:

```markdown
when: standing up, redeploying or debugging aegis on the VPS (dev.apiad.net) — the aegis-server + aegis-web unit pair behind Caddy
```

Replace the topology block with:

```
browser ──HTTPS──► Caddy (vps.apiad.net) ──► reverse_proxy 127.0.0.1:8899
        dev.apiad.net                                    │
                                                  aegis-web.service
                                                  (token + cookie, xterm.js)
                                                         │ unix socket
                                                  aegis-server.service
                                                  WorkingDirectory ~/Workspace
```

Then rewrite the body: one unit pair instead of one unit; `Wants=` not
`Requires=`, with the reason; `--no-autostart` and why; the token as the only
secret (drop every mention of `~/.aegis-web-basicpw` and `basicauth`); the
login URL as a one-time `?t=` exchanged for a cookie rather than a
`?t=`-forever URL; and delete the "SW + basic auth" and PWA-installability
sections. Add the redeploy procedure:

```bash
ssh vps 'cd ~/Workspace/repos/aegis && git pull --ff-only origin main'
ssh vps 'sudo systemctl restart aegis-server aegis-web'
```

- [ ] **Step 3: CHANGELOG, TASKS, statuses**

Add under `## [Unreleased]` in `CHANGELOG.md`, inside the existing `### Changed`:

```markdown
- **The old browser client, `RemoteSessionManager` and `aegis --remote` are
  deleted.** The hand-written JS client and its aegis-aware WebSocket protocol
  are gone, along with the `--remote` TUI that spoke the same protocol and the
  twenty branches it threaded through the TUI. `aegis web` serves browsers and
  ssh serves remote terminals. `aegis web` gains `--no-autostart` for systemd,
  and serves a service worker that unregisters the retired PWA.
```

In `TASKS.md`, change row 2b's `6 not planned` to
`6 shipped <date> (\`<first>\`..\`<last>\`)`, and replace the stage-6 paragraph
with two sentences naming the commits and the verification. Set this plan's
header to `**Status: shipped <date>** (\`<first>\`..\`<last>\`)` and tick every
box. Set `2026-09-07-retire-web-ui-tui-over-web-design.md`'s status line —
which currently reads `stage 5b and stage 6 not planned` — to name both as
shipped.

- [ ] **Step 4: Run every gate**

```bash
cd /home/apiad/Workspace/repos/aegis
rift check
make test
uv run ty check src/
```

Expected: rift 0 errors; the suite at the Global Constraints baseline minus the
deleted tests; `ty` at 350 or below. Read each rc directly.

- [ ] **Step 5: Commit and push**

```bash
cd /home/apiad/Workspace/repos/aegis
git add -u README.md AGENTS.md DESIGN.md TASKS.md CHANGELOG.md docs know-how
git commit -m "docs: the old web client, --remote and the PWA are gone" -- README.md AGENTS.md DESIGN.md TASKS.md CHANGELOG.md docs know-how
git push origin main
```

---

## Done when

- `aegis --help` offers no `--remote`, `--token` or `--tail`, and `aegis serve` still resolves as a hidden alias (Task 1).
- `aegis.tui.remote_manager` and `aegis.tui.ws_client` do not import, and no `_remote_manager` branch survives in `tui/app.py` or `tui/pane.py` (Task 2, plus a real TUI drawn through a real `aegis web`).
- `aegis.web` does not import and `src/aegis/web/` does not exist; a `.aegis.yaml` with a `web:` block and a state dir from the old version still boot (Task 3).
- `aegis web --no-autostart` serves without spawning a daemon, and autostart is still the default (Task 4, mutation-checked on the real command).
- `GET /service-worker.js` returns an unregistering worker with no cookie required (Task 5, mutation-checked).
- `dev.apiad.net` answers 401 without a token, 303 on the login URL, and draws the TUI in a browser; the daemon holds no TCP listener; restarting `aegis-server` does not restart `aegis-web` (Task 6).
- `rift check` is 0 errors; `make test` is at the baseline; `ty check src/` is ≤ 350 (Task 7).

## Deliberately not in this plan

- **The installable PWA.** The new page has no manifest or worker of its own; Task 5's worker only removes the old one. Making the terminal installable is a follow-up.
- **Reaping view files.** Every browser tab leaves `.aegis/state/views/web-<uuid>.json`. A rule for removing ones no tab will reopen is still open.
- **`aegis kill` not waiting for the exit it reports.** Filed in `TASKS.md` on 2026-09-27; pre-existing and unrelated to the deletion.
- **The pre-existing `test_resolve_boot_registers_a_builtin_named_in_workflows` failure.** It belongs to the afk built-in's registry, not here.
