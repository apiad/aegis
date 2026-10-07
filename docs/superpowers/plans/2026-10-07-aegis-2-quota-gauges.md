# aegis 2 quota and host gauges: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the quota gauges (Claude, OpenCode Go) and the host gauges (CPU, RAM, disk, average context) back into aegis 2, in a band on the Fleet view and a Quota section in the session sidebar.

**Architecture:** A `quota` package copied from `legacy/aegis/usage/` feeds a `Quota` object that ticks every 60 s and publishes a wire snapshot on a `quota` channel. A stdlib `HostSampler` publishes on a `host` channel while someone subscribes. The client draws both with one gauge renderer in `js/gauges.js`. `quota.read` exposes the snapshot to agents over `/mcp`.

**Tech Stack:** Python 3.13, asyncio, pydantic, Starlette and uvicorn, plain ES modules (no framework, no build), pytest with pytest-asyncio (`asyncio_mode = "auto"`), Playwright for the browser tests.

**Spec:** `docs/superpowers/specs/2026-10-07-aegis-2-quota-gauges-design.md`

## Global Constraints

- Nothing under `src/aegis/` imports `legacy/`; imports inside `src/aegis/` are relative (`tests/test_imports.py` enforces both).
- Nothing below the CLI calls `Path.cwd()` (`tests/test_no_cwd.py`).
- No new runtime dependency: host sampling is `/proc` and `os.statvfs`, fetches are `urllib`.
- Cache path: `$AEGIS_QUOTA_CACHE`, else `$XDG_CACHE_HOME/aegis/quota`, else `~/.cache/aegis/quota`; one file per provider, `<name>.json`, in the legacy format.
- Cadence: Claude polls every 180 s with a 60 s turn floor; OpenCode Go every 60 s with a 10 s floor; a 429 backs off 300 s, shared through the cache.
- Pace: warning at a projection of 80% or more, critical above 100%; the elapsed fraction is floored at 0.15; level thresholds 80 and 95 when the API gives no severity.
- Labels: providers "Claude" and "OpenCode Go"; windows "5 hours", "week", "month".
- No test may read the real OAuth token or the real cache: `tests/conftest.py` points `CLAUDE_CREDS`, `OPENCODE_AUTH` and `AEGIS_QUOTA_CACHE` into a temp dir for every test. Never set `XDG_CACHE_HOME` in tests; Playwright finds Chromium under it.
- Agents never force a fetch: `quota.read` returns the cached snapshot.
- Code, comments, test names and copy in English. Conventional commits. Stage named paths only.
- Work in the worktree `.claude/worktrees/quota-gauges` on `feat/quota-gauges`. Run `uv sync` there once before Task 1 and `uv run playwright install chromium` before Task 4.

## Review Focus

1. **Boot with the network down and no cache.** A fetch can block 10 s per provider in a thread; `aegis serve` must still come up at once and the band must render without quota. Test: `test_start_returns_before_a_slow_first_fetch` (Task 2).
2. **Credentials that appear after boot** (Alex logs into OpenCode while aegis runs). The provider must show up within one poll interval, without a restart. Test: `test_a_provider_appears_when_its_credentials_do` (Task 2).
3. **A laptop that slept past a reset.** The reset time is in the past: the projection must equal the spend (no division blow-up), the countdown must read "now", and the tick must clamp at the end of the bar. Covered on the Python side by the copied `test_a_finished_window_projects_to_what_it_spent`; Task 4's `countdown` and `elapsed` clamp.
4. **The last browser tab closes.** Host sampling must stop reading `/proc`. Test: `test_nothing_is_sampled_or_published_without_a_subscriber` (Task 3).
5. **A shutdown that cancels the server while a fetch thread is still running** (#72). The cancel must reach the caller. Test: `test_stop_lets_a_cancel_meant_for_its_caller_through` (Task 1).

---

### Task 1: The quota package, copied from the legacy tree

**Files:**
- Create: `src/aegis/quota/core.py`, `src/aegis/quota/claude.py`, `src/aegis/quota/opencode.py`, `src/aegis/quota/__init__.py` (empty for now)
- Create (copied tests): `tests/test_quota_claude.py`, `tests/test_quota_opencode.py`, `tests/test_quota_pace.py`, `tests/test_quota_service.py`
- Modify: `tests/conftest.py`

**Interfaces:**
- Produces, in `aegis.quota.core`: `QuotaWindow(kind, percent, severity, resets_at, is_active)`, `QuotaSnapshot(windows, fetched_at)` with `.window(kind)`, `QuotaProvider(name, label, harness, bar_windows, fetch, read_token, window_spans={}, poll_s=60.0, turn_floor_s=10.0)`, `QuotaError(kind)`, `QuotaState(snapshot=None, age_s=0.0, failure="", retry_in_s=0.0)`, `QuotaService(*, fetch, token_reader, clock=time.monotonic, poll_s=POLL_S, cache=None, wall=time.time)` with `async refresh(*, force=False, min_interval=None)`, `current() -> QuotaState`, `start()`, `async stop()`; `window_pace(window, span_s, *, now) -> float | None`; `pace_severity(window, span_s, *, now) -> str`; `cancel_and_wait(task) -> None` (async); `FAILURE_TEXT: dict[str, str]`; constants `POLL_S`, `BACKOFF_S`, `PACE_WARN_AT`, `PACE_CRIT_AT`, `PACE_FLOOR`.
- Produces `aegis.quota.claude.PROVIDER` (name `"claude"`, label `"Claude"`) and `aegis.quota.opencode.PROVIDER` (name `"opencode-go"`, label `"OpenCode Go"`).

- [x] **Step 1: Isolate every test from the real quota files**

Append to `tests/conftest.py`, after `until`:

```python
@pytest.fixture(autouse=True)
def _no_real_quota(tmp_path_factory, monkeypatch):
    """No test reads the real OAuth token or the machine's quota cache. Reading
    the token would let a test reach a vendor's usage endpoint, whose 429s
    starve the gauges of the aegis Alex is running (#41); writing the cache
    would overwrite what that aegis shows. Not XDG_CACHE_HOME: Playwright looks
    for its Chromium under it. The browser tests' `aegis serve` inherits these."""
    off = tmp_path_factory.mktemp("quota")
    monkeypatch.setenv("CLAUDE_CREDS", str(off / "claude-credentials.json"))
    monkeypatch.setenv("OPENCODE_AUTH", str(off / "opencode-auth.json"))
    monkeypatch.setenv("AEGIS_QUOTA_CACHE", str(off / "cache"))
```

- [x] **Step 2: Copy the legacy tests with their imports moved**

```bash
cd .claude/worktrees/quota-gauges
moved() {
  sed -e 's/from aegis\.usage\.quota_claude import/from aegis.quota.claude import/' \
      -e 's/from aegis\.usage\.quota_opencode import/from aegis.quota.opencode import/' \
      -e 's/from aegis\.usage\.quota import/from aegis.quota.core import/'
}
awk '/^def test_a_provider_with_no_reading_gets_a_placeholder_gauge_only_on_request/{exit} {print}' legacy/tests/test_quota.py | moved > tests/test_quota_claude.py
moved < legacy/tests/test_quota_opencode.py > tests/test_quota_opencode.py
awk '/^# --- what each renderer does with it/{exit} {print}' legacy/tests/test_quota_pace.py | moved > tests/test_quota_pace.py
awk '/^@pytest.mark.asyncio$/{held=$0; next} /^async def test_build_services_wires_each_provider_cadence_and_cache/{exit} {if (held) {print held; held=""} print}' legacy/tests/test_quota_service.py | moved > tests/test_quota_service.py
grep -n 'aegis\.usage\|aegis\.themes\|aegis\.tui\|quota_gauges\|format_quota_bar\|quota_lines' tests/test_quota_*.py; echo "leftovers rc=$?"
```

Expected: no matches and `leftovers rc=1`. Strip trailing blank lines at the end of each file if `ruff format` asks to.

- [x] **Step 3: Add the two new service tests**

Append to `tests/test_quota_service.py`:

```python
async def test_a_backoff_reports_how_long_until_the_next_try():
    from aegis.quota.core import BACKOFF_S

    c = Clock()
    svc = _service(c, [QuotaError("rate_limited")])
    await svc.refresh()
    assert svc.current().retry_in_s == pytest.approx(BACKOFF_S)
    c.advance(100)
    assert svc.current().retry_in_s == pytest.approx(BACKOFF_S - 100)
    c.advance(BACKOFF_S)
    assert svc.current().retry_in_s == 0.0


async def test_stop_lets_a_cancel_meant_for_its_caller_through():
    """#72: the legacy stop() swallowed every CancelledError, including one
    aimed at whoever was awaiting stop(), so a shutdown that cancelled it hung
    on as if nothing had happened."""
    import asyncio

    svc = QuotaService(fetch=lambda token: None, token_reader=lambda: None)

    async def dies_slowly():  # as a poller does with a fetch thread in flight
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            await asyncio.sleep(0.2)

    svc._task = asyncio.create_task(dies_slowly())
    await asyncio.sleep(0)
    caller = asyncio.create_task(svc.stop())
    await asyncio.sleep(0.05)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
```

- [x] **Step 4: Run the copied tests to see them fail**

Run: `uv run pytest -q tests/test_quota_claude.py tests/test_quota_opencode.py tests/test_quota_pace.py tests/test_quota_service.py`
Expected: collection errors, `ModuleNotFoundError: No module named 'aegis.quota'`.

- [x] **Step 5: Copy the core, without the renderers**

```bash
mkdir -p src/aegis/quota
awk '/^def _countdown/{exit} {print}' legacy/aegis/usage/quota.py > src/aegis/quota/core.py
: > src/aegis/quota/__init__.py
```

Then edit `src/aegis/quota/core.py`:

1. Replace the module docstring with:

```python
"""Subscription-quota core, shared by every provider.

A provider module (``claude``, ``opencode``) knows one vendor's credentials,
endpoint and payload shape, and produces a ``QuotaSnapshot``. Polling,
staleness, severity and pace live here and know nothing about any vendor.
Copied from the legacy tree's ``aegis/usage/quota.py`` without its Rich
renderers; ``aegis.quota.Quota`` turns readings into the wire snapshot.

``QuotaProvider`` is the seam: a third vendor is a new module plus an entry in
``aegis.quota.PROVIDERS``, not a change here.
"""
```

2. Replace `from typing import TYPE_CHECKING, Callable, Mapping` with `from typing import Callable, Mapping`, add `import asyncio` to the stdlib imports, and delete the two lines

```python
if TYPE_CHECKING:
    from aegis.fleet.models import QuotaGauge
```

3. In `QuotaState`, add the field after `failure`:

```python
    retry_in_s: float = 0.0  # >0 while a 429 backoff runs
```

4. Replace `QuotaService.stop` with:

```python
    async def stop(self) -> None:
        task, self._task = self._task, None
        await cancel_and_wait(task)
```

5. Replace `QuotaService.current` with:

```python
    def current(self) -> QuotaState:
        now = self._clock()
        age = 0.0
        if self._snapshot is not None:
            age = max(0.0, now - self._snapshot.fetched_at)
        return QuotaState(
            snapshot=self._snapshot,
            age_s=age,
            failure=self._failure,
            retry_in_s=max(0.0, self._backoff_until - now),
        )
```

6. Rename `_FAILURE_TEXT` to `FAILURE_TEXT` (it is public now: `aegis.quota` reads it).

7. Add, right after `_worse`:

```python
async def cancel_and_wait(task: asyncio.Task | None) -> None:
    """Cancel a background task and wait for it to finish.

    The task's own CancelledError is expected and swallowed. One aimed at our
    caller, which arrives while we wait, is re-raised: the legacy ``stop()``
    swallowed both, so a shutdown that cancelled it carried on as if nothing
    had happened (#72). Any other error from a dying task is swallowed, because
    it must not break a shutdown.
    """
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        current = asyncio.current_task()
        if current is not None and current.cancelling():
            raise
    except Exception:  # noqa: BLE001
        pass
```

Delete the now-unused `import asyncio` lines inside `QuotaService.start`, `_loop` and `refresh` only if ruff reports them as redefinitions; otherwise leave them.

- [x] **Step 6: Copy the two providers**

```bash
for p in claude opencode; do
  sed -e 's/^from aegis\.usage\.quota import (/from .core import (/' legacy/aegis/usage/quota_$p.py > src/aegis/quota/$p.py
done
grep -n 'aegis\.usage\|status bar' src/aegis/quota/*.py
```

Then edit:

- `src/aegis/quota/claude.py`: `label="cc"` becomes `label="Claude"`; `bar_windows=(("session", "5h"), ("weekly_all", "wk"))` becomes `bar_windows=(("session", "5 hours"), ("weekly_all", "week"))`; replace the comment above `bar_windows` with `# The two windows the gauges draw; the payload has more.`; in the docstring, "degrades to a message in the status bar" becomes "degrades to a note on the gauges".
- `src/aegis/quota/opencode.py`: `label="oc"` becomes `label="OpenCode Go"`; `bar_windows=(("rolling", "5h"), ("weekly", "wk"), ("monthly", "mo"))` becomes `bar_windows=(("rolling", "5 hours"), ("weekly", "week"), ("monthly", "month"))`; the same docstring change.

- [x] **Step 7: Run the tests**

Run: `uv run pytest -q tests/test_quota_claude.py tests/test_quota_opencode.py tests/test_quota_pace.py tests/test_quota_service.py tests/test_imports.py`
Expected: all pass. If a copied test asserts a legacy label (`"cc"`, `"5h"`), change the assertion to the new label and say so in the commit body.

- [x] **Step 8: Lint, type-check, commit**

```bash
uv run ruff format src/aegis/quota tests/test_quota_*.py tests/conftest.py
uv run ruff check src/aegis/quota tests/test_quota_*.py tests/conftest.py
uv run ty check src/
git add src/aegis/quota tests/test_quota_claude.py tests/test_quota_opencode.py tests/test_quota_pace.py tests/test_quota_service.py tests/conftest.py
git commit -F - <<'EOF'
feat(quota): copy the legacy quota core and its two providers (#146)

QuotaService, pace and the Claude and OpenCode Go parsers, copied from
legacy/aegis/usage without the Rich renderers, with their tests. stop() now
lets a cancel meant for its caller through (#72), and QuotaState says how
long a 429 backoff has left. Every test runs with the quota credentials and
cache pointed into a temp dir.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 2: `Quota`, the `quota` channel and `quota.read`

**Files:**
- Modify: `src/aegis/quota/__init__.py`
- Modify: `src/aegis/app.py` (constructor, `boot`, `shutdown`, `_resolve`, `_register`, module docstring)
- Modify: `src/aegis/registry.py:56-72` (`self.quota = None`, `turn_ended`)
- Create: `tests/test_quota_wire.py`
- Modify: `tests/test_agents.py` (tool list, one new test)

**Interfaces:**
- Consumes: everything Task 1 produces.
- Produces: `aegis.quota.Quota(publish, *, providers=PROVIDERS, cache=None, clock=time.monotonic, wall=time.time)` with `snapshot() -> dict`, `check() -> None`, `async tick()`, `start()`, `async stop()`, `turn_ended()`; `aegis.quota.cache_dir() -> Path`; `aegis.quota.PROVIDERS`; `aegis.quota.TICK_S = 60.0`. `App.quota`. Channel `quota`: snapshot `{"providers": [...]}`, patches `[{"set": snapshot}]`. Wire provider: `{"name", "label", "state": "ok"|"stale"|"failed", "note", "read_at": int|None, "retry_at": int|None, "windows": [{"kind", "label", "percent", "severity", "projected": int|None, "starts_at": int|None, "resets_at": int|None}]}`. Operation `quota.read` (agent tool `quota_read`).

- [x] **Step 1: Write the failing tests**

Create `tests/test_quota_wire.py`:

```python
"""Quota on the wire: what the `quota` channel and `quota.read` carry, and
when the channel publishes."""

import asyncio
import dataclasses
import threading
from datetime import datetime, timezone

import pytest

from aegis.quota import Quota, cache_dir
from aegis.quota.core import QuotaError, QuotaProvider, QuotaSnapshot, QuotaWindow

from .conftest import until

WALL = 1_800_000_000.0
H5, WK = 5 * 3600, 7 * 86400


class Clocks:
    """A monotonic clock and a wall clock that advance together."""

    def __init__(self):
        self.mono, self.wall = 1000.0, WALL

    def advance(self, dt):
        self.mono += dt
        self.wall += dt


def _at(clocks, seconds_from_now):
    return datetime.fromtimestamp(clocks.wall + seconds_from_now, timezone.utc)


def provider(clocks, results, calls, *, token="tok"):
    """A Claude-shaped provider. Each entry of `results` is an exception to
    raise, or (percent of the 5-hour window, seconds until it resets)."""

    def fetch(tok, **kw):
        calls.append(tok)
        item = results.pop(0)
        if isinstance(item, Exception):
            raise item
        pct, remaining = item
        return QuotaSnapshot(
            windows=(
                QuotaWindow("session", pct, "normal", _at(clocks, remaining), True),
                QuotaWindow("weekly_all", 47.0, "normal", _at(clocks, 2 * 86400), True),
            ),
            fetched_at=clocks.mono,
        )

    return QuotaProvider(
        name="claude",
        label="Claude",
        harness="claude-code",
        bar_windows=(("session", "5 hours"), ("weekly_all", "week")),
        fetch=fetch,
        read_token=token if callable(token) else (lambda path=None: token),
        window_spans={"session": H5, "weekly_all": WK},
        poll_s=180.0,
        turn_floor_s=60.0,
    )


def make(tmp_path, clocks, results, *, token="tok"):
    calls, sent = [], []
    q = Quota(
        lambda channel, ops: sent.append((channel, ops)),
        providers=(provider(clocks, results, calls, token=token),),
        cache=tmp_path,
        clock=lambda: clocks.mono,
        wall=lambda: clocks.wall,
    )
    return q, calls, sent


async def test_a_reading_goes_on_the_wire_decided(tmp_path):
    c = Clocks()
    q, calls, sent = make(tmp_path, c, [(71.0, 3 * 3600 + 6 * 60)])
    await q.tick()
    snap = q.snapshot()
    (claude,) = snap["providers"]
    assert claude | {"windows": None} == {
        "name": "claude",
        "label": "Claude",
        "state": "ok",
        "note": "",
        "read_at": round(WALL),
        "retry_at": None,
        "windows": None,
    }
    five, week = claude["windows"]
    # 71% with 38% of the window gone lands at 187% by the reset.
    assert five == {
        "kind": "session",
        "label": "5 hours",
        "percent": 71.0,
        "severity": "critical",
        "projected": 187,
        "starts_at": round(WALL + 11160 - H5),
        "resets_at": round(WALL + 11160),
    }
    assert week["severity"] == "normal" and week["projected"] == 66
    assert sent == [("quota", [{"set": snap}])]


async def test_nothing_is_published_until_the_snapshot_changes(tmp_path):
    c = Clocks()
    q, calls, sent = make(tmp_path, c, [(71.0, 11160)])
    await q.tick()
    await q.tick()
    assert len(sent) == 1 and len(calls) == 1
    c.advance(60)  # the projection moves with the clock, without a fetch
    await q.tick()
    assert len(sent) == 2 and len(calls) == 1
    assert sent[-1][1][0]["set"]["providers"][0]["windows"][0]["projected"] == 185


async def test_a_failing_fetch_after_a_reading_is_stale_and_unprojected(tmp_path):
    c = Clocks()
    q, calls, sent = make(tmp_path, c, [(71.0, 11160), QuotaError("unreachable")])
    await q.tick()
    c.advance(181)
    await q.tick()
    (claude,) = q.snapshot()["providers"]
    assert claude["state"] == "stale" and claude["note"] == "unreachable"
    assert claude["read_at"] == round(WALL)
    for w in claude["windows"]:
        assert (w["severity"], w["projected"], w["starts_at"]) == ("normal", None, None)


async def test_no_credentials_is_absent_and_a_failure_is_a_note(tmp_path):
    q, _, _ = make(tmp_path / "a", Clocks(), [], token=None)
    await q.tick()
    assert q.snapshot() == {"providers": []}

    q, _, _ = make(tmp_path / "b", Clocks(), [QuotaError("unauthorized")])
    await q.tick()
    (claude,) = q.snapshot()["providers"]
    assert (claude["state"], claude["note"], claude["windows"]) == (
        "failed",
        "auth expired",
        [],
    )


async def test_a_rate_limit_says_when_it_will_retry(tmp_path):
    q, _, _ = make(tmp_path, Clocks(), [QuotaError("rate_limited")])
    await q.tick()
    (claude,) = q.snapshot()["providers"]
    assert (claude["note"], claude["retry_at"]) == ("rate limited", round(WALL + 300))


async def test_reading_the_snapshot_never_fetches(tmp_path):
    """`quota.read` is this call; an agent asking must not cost a request."""
    q, calls, _ = make(tmp_path, Clocks(), [(71.0, 11160)])
    await q.tick()
    for _ in range(5):
        q.snapshot()
    assert len(calls) == 1


async def test_a_provider_appears_when_its_credentials_do(tmp_path):
    token = {"value": None}
    c = Clocks()
    q, calls, _ = make(
        tmp_path, c, [(10.0, 11160)], token=lambda path=None: token["value"]
    )
    await q.tick()
    assert q.snapshot()["providers"] == []
    token["value"] = "tok"
    c.advance(181)  # the token is read again on the next poll, not every tick
    await q.tick()
    assert [p["state"] for p in q.snapshot()["providers"]] == ["ok"]


async def test_a_turn_end_refreshes_no_sooner_than_the_turn_floor(tmp_path):
    c = Clocks()
    q, calls, sent = make(tmp_path, c, [(50.0, 11160), (55.0, 11100)])
    q.start()
    await until(lambda: len(calls) == 1, what="the first tick")
    q.turn_ended()
    await asyncio.sleep(0.05)
    assert len(calls) == 1
    c.advance(61)
    q.turn_ended()
    await until(lambda: len(calls) == 2, what="the turn-end refresh")
    await until(lambda: sent[-1][1][0]["set"]["providers"][0]["windows"][0]["percent"] == 55.0)
    await q.stop()


async def test_start_returns_before_a_slow_first_fetch(tmp_path):
    release = threading.Event()
    c = Clocks()
    calls = []
    slow = provider(c, [(10.0, 11160)], calls)
    fetch = slow.fetch

    def blocking(tok, **kw):
        release.wait(5)
        return fetch(tok, **kw)

    q = Quota(
        lambda *a: None,
        providers=(dataclasses.replace(slow, fetch=blocking),),
        cache=tmp_path,
        clock=lambda: c.mono,
        wall=lambda: c.wall,
    )
    q.start()
    assert q.snapshot() == {"providers": []}
    release.set()
    await until(lambda: q.snapshot()["providers"], what="the reading")
    await q.stop()


def test_the_cache_dir_takes_the_override_then_xdg(monkeypatch, tmp_path):
    monkeypatch.delenv("AEGIS_QUOTA_CACHE")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert cache_dir() == tmp_path / "xdg" / "aegis" / "quota"
    monkeypatch.setenv("AEGIS_QUOTA_CACHE", str(tmp_path / "here"))
    assert cache_dir() == tmp_path / "here"
```

In `tests/test_agents.py`, add `"quota_read",` to the set in `test_the_tools_are_named_after_their_operations_and_take_no_handle`, and add after `test_a_call_acts_as_its_own_session`:

```python
async def test_quota_read_returns_the_snapshot(world):
    a = await world.spawn()
    said = await turn(a, mcp("quota_read"))
    # conftest leaves no credentials, so no provider has anything to say.
    assert json.loads(said.removeprefix("mcp ok: ")) == {"providers": []}
```

- [x] **Step 2: Run them to see them fail**

Run: `uv run pytest -q tests/test_quota_wire.py tests/test_agents.py -k "quota or tools_are_named"`
Expected: `ImportError: cannot import name 'Quota' from 'aegis.quota'`.

- [x] **Step 3: Write `Quota`**

Replace `src/aegis/quota/__init__.py` with:

```python
"""Quota: how much of each subscription window is left, for the gauges.

``Quota`` holds one ``QuotaService`` per provider and runs one loop. Every
``TICK_S`` it asks each service to refresh, and the service's own floor decides
whether that really fetches (Claude every 180 s, OpenCode Go every 60 s). So one
loop carries both the fetch cadence and the re-evaluation of pace, which moves
with the clock. It then builds the wire snapshot and publishes it on the
``quota`` channel if it differs from the last one published; ``projected`` and
the times are rounded so that happens at most once a tick.

Python decides severity and the projection; the browser draws the tick and the
countdown from ``starts_at`` and ``resets_at`` (DESIGN.md, "Python decides, the
browser draws"). Reading the snapshot never fetches, so ``quota.read`` costs an
agent nothing against the vendor.

The cache under ``cache_dir()`` is shared by every aegis process on the
machine, the legacy tree's included: quota is an account property, and the
Claude endpoint 429s when several pollers ask (#41).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from .claude import PROVIDER as CLAUDE
from .core import (
    FAILURE_TEXT,
    QuotaProvider,
    QuotaService,
    QuotaState,
    cancel_and_wait,
    pace_severity,
    window_pace,
)
from .opencode import PROVIDER as OPENCODE_GO

log = logging.getLogger("aegis.quota")

PROVIDERS: tuple[QuotaProvider, ...] = (CLAUDE, OPENCODE_GO)
TICK_S = 60.0
Publish = Callable[[str, list[dict]], None]


def cache_dir() -> Path:
    """Where every aegis process on the machine shares its last reading."""
    if override := os.environ.get("AEGIS_QUOTA_CACHE"):
        return Path(override)
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "aegis" / "quota"


def _retry_at(state: QuotaState, wall: float) -> int | None:
    return round(wall + state.retry_in_s) if state.retry_in_s > 0 else None


def provider_wire(
    provider: QuotaProvider, state: QuotaState, *, now: datetime, wall: float
) -> dict | None:
    """One provider on the wire, or None when it has nothing to say: no
    credentials (a rail not in use), or no reading and no failure yet."""
    head = {"name": provider.name, "label": provider.label}
    note = FAILURE_TEXT.get(state.failure, state.failure)
    if state.snapshot is None:
        if not state.failure or state.failure == "no_credentials":
            return None
        return head | {
            "state": "failed",
            "note": note,
            "read_at": None,
            "retry_at": _retry_at(state, wall),
            "windows": [],
        }
    # A stale reading does not get to speak about pace: its percent is frozen
    # while the clock runs on, so the projection would fall on its own.
    stale = bool(state.failure)
    windows = []
    for kind, label in provider.bar_windows:
        w = state.snapshot.window(kind)
        if w is None:
            continue
        span = None if stale else provider.window_spans.get(kind)
        projected = window_pace(w, span, now=now)
        resets = None if w.resets_at is None else w.resets_at.timestamp()
        starts = None
        if projected is not None and resets is not None and span:
            starts = round(resets - span)
        windows.append(
            {
                "kind": kind,
                "label": label,
                "percent": w.percent,
                "severity": "normal" if stale else pace_severity(w, span, now=now),
                "projected": None if projected is None else round(projected),
                "starts_at": starts,
                "resets_at": None if resets is None else round(resets),
            }
        )
    return head | {
        "state": "stale" if stale else "ok",
        "note": note if stale else "",
        "read_at": round(wall - state.age_s),
        "retry_at": _retry_at(state, wall),
        "windows": windows,
    }


class Quota:
    def __init__(
        self,
        publish: Publish,
        *,
        providers: tuple[QuotaProvider, ...] = PROVIDERS,
        cache: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self._publish = publish
        self._providers = providers
        self._wall = wall
        root = cache_dir() if cache is None else cache
        self.services = {
            p.name: QuotaService(
                fetch=p.fetch,
                token_reader=p.read_token,
                clock=clock,
                poll_s=p.poll_s,
                cache=root / f"{p.name}.json",
                wall=wall,
            )
            for p in providers
        }
        self._last: dict | None = None
        self._task: asyncio.Task | None = None
        self._nudges: set[asyncio.Task] = set()

    def snapshot(self) -> dict:
        wall = self._wall()
        now = datetime.fromtimestamp(wall, timezone.utc)
        providers = []
        for p in self._providers:
            wire = provider_wire(p, self.services[p.name].current(), now=now, wall=wall)
            if wire is not None:
                providers.append(wire)
        return {"providers": providers}

    def check(self) -> None:
        snap = self.snapshot()
        if snap != self._last:
            self._last = snap
            self._publish("quota", [{"set": snap}])

    async def tick(self) -> None:
        for p in self._providers:
            await self.services[p.name].refresh()
        self.check()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # a tick must never end the loop
                log.exception("quota tick failed")
            await asyncio.sleep(TICK_S)

    async def stop(self) -> None:
        task, self._task = self._task, None
        for t in list(self._nudges):
            await cancel_and_wait(t)
        await cancel_and_wait(task)

    def turn_ended(self) -> None:
        """A Claude turn just spent quota: refresh Claude before its cadence
        would, but no sooner than its turn floor after the last fetch."""
        if self._task is None:
            return
        for p in self._providers:
            if p.name == CLAUDE.name:
                t = asyncio.create_task(self._nudge(p))
                self._nudges.add(t)
                t.add_done_callback(self._nudges.discard)

    async def _nudge(self, p: QuotaProvider) -> None:
        await self.services[p.name].refresh(min_interval=p.turn_floor_s)
        self.check()
```

- [x] **Step 4: Wire it into the app and the registry**

In `src/aegis/registry.py`, in `Registry.__init__` after `self.queues = None`, add `self.quota = None`. In `turn_ended`, add at the end:

```python
        if self.quota is not None:
            self.quota.turn_ended()
```

In `src/aegis/app.py`:
- add `from .quota import Quota` to the imports;
- in `App.__init__`, after `self.queues = Queues(...)`, add `self.quota = Quota(self.publish)`, and after the `reg.tokens, reg.monitors, ...` assignment add `reg.quota = self.quota`;
- in `boot`, add `self.quota.start()` as the last line;
- in `shutdown`, add `await self.quota.stop()` as the first line;
- in `_resolve`, before `return None`, add `if name == "quota": return self.quota.snapshot`;
- in `_register`, after `server_version`, add:

```python
        @r.op("quota.read", agent=True)
        async def quota_read(_, caller):
            """How much of each subscription window is left: per provider, each
            window's percent used, severity, projected percent at reset and
            reset time (epoch seconds). Reads the last reading; never asks the
            vendor, so calling it costs nothing."""
            return self.quota.snapshot()
```

- in the module docstring, add `quota.read` to the operations and the channel `quota` (each provider's windows; patches `set`).

- [x] **Step 5: Run the tests**

Run: `uv run pytest -q tests/test_quota_wire.py tests/test_agents.py tests/test_registry.py tests/test_imports.py tests/test_no_cwd.py`
Expected: all pass.

- [x] **Step 6: Lint, type-check, commit**

```bash
uv run ruff format src/aegis/quota src/aegis/app.py src/aegis/registry.py tests/test_quota_wire.py tests/test_agents.py
uv run ruff check src/ tests/test_quota_wire.py tests/test_agents.py
uv run ty check src/
git add src/aegis/quota/__init__.py src/aegis/app.py src/aegis/registry.py tests/test_quota_wire.py tests/test_agents.py
git commit -F - <<'EOF'
feat(quota): the quota channel and quota.read (#146)

Quota ticks every 60 s, lets each provider's floor decide whether to fetch,
and publishes the wire snapshot when it changes: severity and projection
decided in Python, starts_at and resets_at for the browser's tick and
countdown. A Claude turn end refreshes Claude no sooner than 60 s after the
last fetch. Agents read the snapshot as quota_read, which never fetches.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 3: `HostSampler` and the `host` channel

**Files:**
- Create: `src/aegis/host.py`
- Modify: `src/aegis/app.py` (constructor, `boot`, `shutdown`, `_resolve`, docstring)
- Create: `tests/test_host.py`

**Interfaces:**
- Consumes: `Channels.subscribers(channel) -> int` (`src/aegis/channels.py`), `App.publish`, `roots.config_root`.
- Produces: `aegis.host.HostSampler(publish, subscribers, disk_path, *, proc=Path("/proc"), interval=INTERVAL_S)` with `sample() -> dict | None`, `snapshot() -> dict | None`, `check() -> None`, `start()`, `async stop()`. Channel `host`: snapshot `{"cpu": int, "ram": {"pct", "used_gb", "total_gb"}, "disk": {"pct", "used_gb", "total_gb"} | None}` or `null`; patches `[{"set": snapshot}]`. `App.host`.

- [x] **Step 1: Write the failing tests**

Create `tests/test_host.py`:

```python
"""Host meters: CPU, RAM and disk from /proc and statvfs."""

from pathlib import Path

from aegis.host import HostSampler

STAT = "cpu  {busy} 0 0 {idle} 0 0 0 0 0 0\ncpu0 1 0 0 1 0 0 0 0 0 0\n"
MEMINFO = "MemTotal:       {total} kB\nMemFree:          1000 kB\nMemAvailable:   {avail} kB\n"


def proc(tmp_path: Path, *, busy=100, idle=900, total=32_000_000, avail=24_000_000):
    p = tmp_path / "proc"
    p.mkdir(exist_ok=True)
    (p / "stat").write_text(STAT.format(busy=busy, idle=idle))
    (p / "meminfo").write_text(MEMINFO.format(total=total, avail=avail))
    return p


def sampler(tmp_path, p, subscribers=1):
    sent = []
    h = HostSampler(
        lambda channel, ops: sent.append((channel, ops)),
        lambda channel: subscribers if channel == "host" else 0,
        tmp_path,
        proc=p,
    )
    return h, sent


def test_cpu_is_the_busy_share_between_two_samples(tmp_path):
    p = proc(tmp_path, busy=100, idle=900)
    h, _ = sampler(tmp_path, p)
    assert h.sample()["cpu"] == 10  # since boot, on the first sample
    proc(tmp_path, busy=160, idle=940)  # +60 busy, +40 idle
    assert h.sample()["cpu"] == 60


def test_ram_is_total_minus_available(tmp_path):
    h, _ = sampler(tmp_path, proc(tmp_path, total=32 * 1024 * 1024, avail=24 * 1024 * 1024))
    assert h.sample()["ram"] == {"pct": 25, "used_gb": 8.0, "total_gb": 32.0}


def test_disk_reads_the_filesystem_of_the_given_path(tmp_path):
    h, _ = sampler(tmp_path, proc(tmp_path))
    disk = h.sample()["disk"]
    assert 0 <= disk["pct"] <= 100 and disk["total_gb"] > 0


def test_no_proc_means_no_snapshot(tmp_path):
    h, sent = sampler(tmp_path, tmp_path / "missing")
    assert h.snapshot() is None
    h.check()
    assert sent == []


def test_nothing_is_sampled_or_published_without_a_subscriber(tmp_path):
    p = proc(tmp_path)
    h, sent = sampler(tmp_path, p, subscribers=0)
    (p / "stat").unlink()  # a read would now fail loudly in the test
    h.check()
    assert sent == []


def test_a_change_in_a_whole_percent_publishes_and_nothing_else_does(tmp_path):
    p = proc(tmp_path)
    h, sent = sampler(tmp_path, p)
    h.check()
    assert len(sent) == 1 and sent[0][0] == "host"
    proc(tmp_path, busy=200, idle=1800)  # same 10% busy share
    h.check()
    assert len(sent) == 1
    proc(tmp_path, busy=200, idle=1800, avail=16_000_000)  # RAM moves
    h.check()
    assert len(sent) == 2
    assert sent[-1][1] == [{"set": h.snapshot()}]
```

- [x] **Step 2: Run them to see them fail**

Run: `uv run pytest -q tests/test_host.py`
Expected: `ModuleNotFoundError: No module named 'aegis.host'`.

- [x] **Step 3: Write `src/aegis/host.py`**

```python
"""Host meters for the Fleet band: CPU, RAM, and the disk that holds aegis.

Read from /proc and ``os.statvfs``, stdlib only. Sampled every ``INTERVAL_S``
and only while someone subscribes to the ``host`` channel, so a server nobody
watches reads nothing. Published when a whole-percent value changes. Where
/proc is missing (macOS) the snapshot is None and the client hides the meters.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from pathlib import Path

from .quota.core import cancel_and_wait

INTERVAL_S = 5.0
GB = 1024**3
Publish = Callable[[str, list[dict]], None]


def cpu_times(text: str) -> tuple[int, int] | None:
    """(busy, total) jiffies from the aggregate ``cpu`` line of /proc/stat.
    Idle is idle plus iowait; guest time is already inside user and nice, so
    only the first eight columns count."""
    for line in text.splitlines():
        if line.startswith("cpu "):
            v = [int(x) for x in line.split()[1:9]]
            idle = v[3] + (v[4] if len(v) > 4 else 0)
            total = sum(v)
            return total - idle, total
    return None


def memory(text: str) -> dict | None:
    kb: dict[str, int] = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            kb[name] = int(parts[0])
    total, avail = kb.get("MemTotal"), kb.get("MemAvailable")
    if not total or avail is None:
        return None
    used = (total - avail) * 1024
    return {
        "pct": round(100 * used / (total * 1024)),
        "used_gb": round(used / GB, 1),
        "total_gb": round(total * 1024 / GB, 1),
    }


def disk(path: Path) -> dict | None:
    """Used and total for the filesystem holding ``path``, the way ``df``
    counts them: the root-reserved blocks are neither used nor available."""
    try:
        st = os.statvfs(path)
    except OSError:
        return None
    used = (st.f_blocks - st.f_bfree) * st.f_frsize
    avail = st.f_bavail * st.f_frsize
    if used + avail <= 0:
        return None
    return {
        "pct": round(100 * used / (used + avail)),
        "used_gb": round(used / GB),
        "total_gb": round(st.f_blocks * st.f_frsize / GB),
    }


def _key(snap: dict | None) -> tuple | None:
    if snap is None:
        return None
    d = snap["disk"]
    return snap["cpu"], snap["ram"]["pct"], None if d is None else d["pct"]


class HostSampler:
    def __init__(
        self,
        publish: Publish,
        subscribers: Callable[[str], int],
        disk_path: Path,
        *,
        proc: Path = Path("/proc"),
        interval: float = INTERVAL_S,
    ) -> None:
        self._publish = publish
        self._subscribers = subscribers
        self._disk_path = disk_path
        self._proc = proc
        self._interval = interval
        self._prev_cpu: tuple[int, int] | None = None
        self._last: dict | None = None
        self._task: asyncio.Task | None = None

    def sample(self) -> dict | None:
        try:
            stat = (self._proc / "stat").read_text()
            meminfo = (self._proc / "meminfo").read_text()
        except OSError:
            return None
        times, mem = cpu_times(stat), memory(meminfo)
        if times is None or mem is None:
            return None
        prev, self._prev_cpu = self._prev_cpu, times
        busy, total = times if prev is None else (times[0] - prev[0], times[1] - prev[1])
        return {
            "cpu": round(100 * busy / total) if total > 0 else 0,
            "ram": mem,
            "disk": disk(self._disk_path),
        }

    def snapshot(self) -> dict | None:
        if self._last is None:
            self._last = self.sample()
        return self._last

    def check(self) -> None:
        if not self._subscribers("host"):
            return
        snap = self.sample()
        if _key(snap) != _key(self._last):
            self._last = snap
            self._publish("host", [{"set": snap}])

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while True:
            self.check()
            await asyncio.sleep(self._interval)

    async def stop(self) -> None:
        task, self._task = self._task, None
        await cancel_and_wait(task)
```

- [x] **Step 4: Wire it into the app**

In `src/aegis/app.py`: import `from .host import HostSampler`; in `App.__init__` after `self.quota = Quota(self.publish)` add `self.host = HostSampler(self.publish, self.channels.subscribers, roots.config_root)`; in `boot` add `self.host.start()`; in `shutdown` add `await self.host.stop()` after the quota line; in `_resolve` add `if name == "host": return self.host.snapshot`; add the `host` channel to the module docstring.

- [x] **Step 5: Run the tests**

Run: `uv run pytest -q tests/test_host.py tests/test_imports.py tests/test_no_cwd.py tests/test_agents.py -k "host or imports or cwd or tools_are_named"`
Expected: all pass.

- [x] **Step 6: Lint, type-check, commit**

```bash
uv run ruff format src/aegis/host.py src/aegis/app.py tests/test_host.py
uv run ruff check src/ tests/test_host.py
uv run ty check src/
git add src/aegis/host.py src/aegis/app.py tests/test_host.py
git commit -F - <<'EOF'
feat(host): CPU, RAM and disk on a host channel (#146)

Sampled from /proc and statvfs every 5 s, only while someone subscribes,
and published when a whole percent changes. None where /proc is missing.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 4: The Fleet band and the sidebar's Quota section

**Files:**
- Create: `src/aegis/client/js/gauges.js`
- Modify: `src/aegis/client/js/fleet.js` (new export `renderBand`)
- Modify: `src/aegis/client/js/app.js` (subscriptions, `drawBand`, `drawSideQuota`, timer)
- Modify: `src/aegis/client/index.html` (band, sidebar section)
- Modify: `src/aegis/client/css/base.css` (gauge rules)
- Modify: `tests/test_browser.py` (fixture and one test)

**Interfaces:**
- Consumes: the `quota` and `host` channels from Tasks 2 and 3; `dotClass(state)` from `js/tabs.js`; `conn.subscribe(channel, onSnapshot, onPatch)` returning an unsubscribe function; `conn.server`.
- Produces: `gauges.js` exports `PROJECT_FROM`, `countdown(seconds)`, `elapsed(w, now)`, `hostSeverity(pct)`, `quotaRow(p, w, now)`, `quotaSideRow(p, w, now)`, `hostRow(label, pct, tail)`, `noteRow(note)`, `quotaHeading(providers, now)`, `providerLine(p, now)`. `fleet.js` exports `renderBand(band, { metas, quota, host, server, now })`. DOM ids: `#band`, `#band-server`, `#band-counts`, `#band-host`, `#band-quota-col`, `#band-quota-age`, `#band-quota`, `#s-quota-sec`, `#s-quota`. Each quota row carries `data-kind` (the window's `kind`).

- [x] **Step 1: Write the failing browser test**

In `tests/test_browser.py`, add `from datetime import datetime, timezone` to the imports, and after the `server` fixture:

```python
def seed_quota() -> None:
    """A Claude reading fresh enough that the server adopts it without asking,
    and an OpenCode Go reading 14 minutes old behind a live 429 backoff. The
    paths are conftest's temp ones, inherited by `aegis serve`."""
    now = time.time()

    def at(s):
        return datetime.fromtimestamp(now + s, timezone.utc).isoformat()

    cache = Path(os.environ["AEGIS_QUOTA_CACHE"])
    cache.mkdir(parents=True, exist_ok=True)
    Path(os.environ["CLAUDE_CREDS"]).write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "test-token"}})
    )
    Path(os.environ["OPENCODE_AUTH"]).write_text(
        json.dumps({"opencode-go": {"key": "test-key"}})
    )
    window = lambda kind, pct, s: {  # noqa: E731
        "kind": kind, "percent": pct, "severity": "normal",
        "resets_at": at(s), "is_active": True,
    }
    (cache / "claude.json").write_text(json.dumps({
        "fetched_wall": now, "backoff_until_wall": 0.0,
        "windows": [window("session", 71.0, 3 * 3600 + 6 * 60),
                    window("weekly_all", 47.0, 2 * 86400)],
    }))
    (cache / "opencode-go.json").write_text(json.dumps({
        "fetched_wall": now - 840, "backoff_until_wall": now + 240,
        "windows": [window("rolling", 64.0, 3480)],
    }))


@pytest.fixture
def quota_server(tmp_path: Path, fake_claude: str):
    (tmp_path / ".aegis.yaml").write_text(
        "default_agent: opus\nagents:\n  opus: {model: opus, effort: high, permission: full}\n"
    )
    seed_quota()
    s = Server(tmp_path, fake_claude).start()
    yield s
    s.stop()
```

And at the end of the file:

```python
def test_the_fleet_band_and_the_sidebar_show_quota_and_the_host(quota_server, page):
    page.goto(quota_server.url)
    five = page.locator("#band .gauge[data-kind=session]")
    five.wait_for()
    # 71% with 38% of the window gone: on pace for about 187%, so red.
    assert "critical" in five.get_attribute("class")
    assert "71%" in five.inner_text()
    assert re.search(r"→ 18\d%", five.inner_text())
    assert five.locator(".tick").count() == 1
    week = page.locator("#band .gauge[data-kind=weekly_all]")
    assert "normal" in week.get_attribute("class") and "→" not in week.inner_text()
    stale = page.locator("#band .gauge[data-kind=rolling]")
    assert "stale" in stale.get_attribute("class")
    assert stale.locator(".tick").count() == 0
    assert "retrying in" in page.inner_text("#band-quota-age")
    page.wait_for_selector("#band-host .gauge")
    assert "CPU" in page.inner_text("#band-host")

    spawn(page)
    page.wait_for_selector("#s-quota .qrow.critical")
    assert "Claude 5 hours" in page.inner_text("#s-quota")
    assert "OpenCode" not in page.inner_text("#s-quota")
    assert page.errors == []
```

- [x] **Step 2: Run it to see it fail**

Run: `uv run pytest -q tests/test_browser.py -k quota`
Expected: FAIL, a timeout waiting for `#band .gauge[data-kind=session]`.

- [x] **Step 3: Write `src/aegis/client/js/gauges.js`**

```js
// One gauge row, drawn the same in the Fleet band and the session sidebar.
// Severity and the projection arrive decided (aegis/quota); this file turns
// them into markup, plus the two things that move with the clock: the tick at
// the share of the window already gone, and the countdown to its reset.

export const PROJECT_FROM = 80; // the projection prints from here, as quota's PACE_WARN_AT

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
}

// "3h 06m", "2d 4h", "12m"; a reset already past (a laptop that slept) is "now".
export function countdown(seconds) {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return "now";
  const m = Math.floor(s / 60);
  const h = Math.floor(m / 60);
  const d = Math.floor(h / 24);
  if (d >= 1) return `${d}d ${h % 24}h`;
  if (h >= 1) return `${h}h ${String(m % 60).padStart(2, "0")}m`;
  return `${m}m`;
}

export function age(seconds) {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  return `${Math.floor(s / 3600)}h`;
}

function clock(ts) {
  const d = new Date(ts * 1000);
  const hm = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return ts - Date.now() / 1000 > 86400 ? `${d.toLocaleDateString([], { weekday: "short" })} ${hm}` : hm;
}

// The share of the window already gone, clamped to the bar; null draws no tick.
export function elapsed(w, now) {
  if (w.starts_at == null || w.resets_at == null) return null;
  const span = w.resets_at - w.starts_at;
  if (span <= 0) return null;
  return Math.min(1, Math.max(0, (now - w.starts_at) / span));
}

export function hostSeverity(pct) {
  return pct >= 95 ? "critical" : pct >= 80 ? "warning" : "normal";
}

function bar(kind, pct, severity, tick) {
  const b = el("div", `bar ${kind} ${severity}`);
  const fill = el("i");
  fill.style.width = `${Math.min(100, Math.max(0, pct))}%`;
  b.append(fill);
  if (tick != null) {
    const t = el("span", "tick");
    t.style.left = `${tick * 100}%`;
    b.append(t);
  }
  return b;
}

function projection(w) {
  return w.projected != null && w.projected >= PROJECT_FROM ? `→ ${w.projected}%` : "";
}

// The whole reading as a sentence, for the hover title.
function sentence(p, w, now) {
  const gone = elapsed(w, now);
  let s = `${p.label}, ${w.label} window: ${Math.round(w.percent)}% used`;
  if (gone != null) s += `, ${Math.round(gone * 100)}% of the window gone`;
  if (w.projected != null) s += `, on pace for ${w.projected}% at reset`;
  s += ".";
  if (w.resets_at != null) s += ` Resets ${clock(w.resets_at)}, in ${countdown(w.resets_at - now)}.`;
  if (p.state === "stale") s += ` This reading is ${age(now - p.read_at)} old: ${p.note}.`;
  return s;
}

export function quotaRow(p, w, now) {
  const stale = p.state === "stale";
  const g = el("div", `gauge ${w.severity}${stale ? " stale" : ""}`);
  g.dataset.kind = w.kind;
  g.title = sentence(p, w, now);
  const val = el("span", "val");
  val.append(el("b", null, `${Math.round(w.percent)}%`));
  const proj = projection(w);
  if (proj) val.append(el("span", "proj", proj));
  if (w.resets_at != null) val.append(el("span", "rst", `↻ ${countdown(w.resets_at - now)}`));
  g.append(el("span", null, w.label), bar("q", w.percent, w.severity, elapsed(w, now)), val);
  return g;
}

export function quotaSideRow(p, w, now) {
  const stale = p.state === "stale";
  const r = el("div", `qrow ${w.severity}${stale ? " stale" : ""}`);
  r.dataset.kind = w.kind;
  r.title = sentence(p, w, now);
  const kv = el("div", "kv");
  const proj = projection(w);
  kv.append(
    el("span", null, `${p.label} ${w.label}`),
    el("span", `sv ${stale ? "" : w.severity}`, `${Math.round(w.percent)}%${proj ? ` ${proj}` : ""}`),
  );
  const dim = el("div", "kv dim");
  if (w.resets_at != null)
    dim.append(el("span", null, `resets in ${countdown(w.resets_at - now)}`), el("span", null, clock(w.resets_at)));
  r.append(kv, bar("q", w.percent, w.severity, elapsed(w, now)), dim);
  return r;
}

export function hostRow(label, pct, tail) {
  const sev = hostSeverity(pct);
  const g = el("div", `gauge ${sev}`);
  const val = el("span", "val");
  val.append(el("b", null, `${pct}%`));
  if (tail) val.append(el("span", "rst", tail));
  g.append(el("span", null, label), bar("h", pct, sev, null), val);
  return g;
}

export function noteRow(note) {
  const g = el("div", "gauge");
  g.append(el("span"), el("span", "note", note));
  return g;
}

// "Claude", or "Claude · 14m old" for a stale reading.
export function providerLine(p, now) {
  return p.state === "stale" && p.read_at != null ? `${p.label} · ${age(now - p.read_at)} old` : p.label;
}

// The Quota heading: a running backoff wins, else the oldest reading's age.
export function quotaHeading(providers, now) {
  const retry = providers.map((p) => p.retry_at).filter((t) => t != null);
  if (retry.length) return `rate limited, retrying in ${countdown(Math.min(...retry) - now)}`;
  const read = providers.map((p) => p.read_at).filter((t) => t != null);
  return read.length ? `read ${age(now - Math.min(...read))} ago` : "";
}
```

- [x] **Step 4: Add `renderBand` to `src/aegis/client/js/fleet.js`**

Add `import { hostRow, noteRow, providerLine, quotaHeading, quotaRow } from "./gauges.js";` under the existing import, and append:

```js
const STATE_ORDER = ["working", "idle", "error", "stopped"];

// The band over the cards: this server's sessions by state, its host, and
// every provider's quota windows. Counts and the average context come from the
// sessions the client already holds; quota and host from their channels.
export function renderBand(band, { metas, quota, host, server, now }) {
  band.querySelector("#band-server").textContent = server || "server";
  const counts = new Map();
  for (const m of metas) counts.set(m.state, (counts.get(m.state) || 0) + 1);
  const rows = STATE_ORDER.filter((s) => counts.get(s)).map((s) => {
    const d = el("div");
    d.append(el("span", `dot ${dotClass(s)}`), el("b", null, String(counts.get(s))), document.createTextNode(s));
    return d;
  });
  band.querySelector("#band-counts").replaceChildren(...(rows.length ? rows : [el("div", "empty", "no sessions")]));

  const meters = [];
  if (host) {
    meters.push(hostRow("CPU", host.cpu, ""));
    meters.push(hostRow("RAM", host.ram.pct, `${host.ram.used_gb} / ${host.ram.total_gb} GB`));
    if (host.disk) meters.push(hostRow("Disk", host.disk.pct, `${host.disk.used_gb} / ${host.disk.total_gb} GB`));
  }
  const live = metas.filter((m) => m.state !== "stopped" && m.context_window && m.context_tokens);
  if (live.length) {
    const avg = Math.round(live.reduce((a, m) => a + Math.min(100, (100 * m.context_tokens) / m.context_window), 0) / live.length);
    meters.push(hostRow("Context", avg, `avg of ${live.length} live`));
  }
  band.querySelector("#band-host").replaceChildren(...meters);

  const providers = (quota && quota.providers) || [];
  band.querySelector("#band-quota-col").hidden = !providers.length;
  band.querySelector("#band-quota-age").textContent = quotaHeading(providers, now);
  const out = [];
  for (const p of providers) {
    out.push(el("div", "prov", providerLine(p, now)));
    if (p.state === "failed") out.push(noteRow(p.note));
    else for (const w of p.windows) out.push(quotaRow(p, w, now));
  }
  band.querySelector("#band-quota").replaceChildren(...out);
}
```

- [x] **Step 5: Add the markup to `src/aegis/client/index.html`**

Inside `<main class="view v-fleet">`, before `<section class="cards" id="cards">`:

```html
    <section class="band" id="band">
      <div><h5 id="band-server">server</h5><div class="counts" id="band-counts"></div></div>
      <div><h5>Host</h5><div id="band-host"></div></div>
      <div class="q" id="band-quota-col" hidden><h5><span>Quota</span><span class="age" id="band-quota-age"></span></h5><div id="band-quota"></div></div>
    </section>
```

In the sidebar, right after the Context `</div>` (the section holding `#s-cost`):

```html
      <div class="sec" id="s-quota-sec" hidden><h4>Quota</h4><div id="s-quota"></div></div>
```

- [x] **Step 6: Wire it in `src/aegis/client/js/app.js`**

- Change the fleet import to `import { ago, money, renderArchive, renderBand, renderCards } from "./fleet.js";` and add `import { age, quotaSideRow } from "./gauges.js";`.
- Under the `const token = ...` line, add state:

```js
// Quota is one subscription for the page: the band and the sidebar both draw
// it. Host is subscribed only while the Fleet view shows, so a server nobody
// watches samples nothing.
let quota = { providers: [] };
let host = null;
let unsubHost = null;
const nowS = () => Date.now() / 1000;
```

- In the `else` branch after `conn.subscribe("sessions", ...)`, add:

```js
  conn.subscribe(
    "quota",
    (snap) => {
      quota = snap || { providers: [] };
      drawQuota();
    },
    (ops) => {
      for (const op of ops) if (op.set) quota = op.set;
      drawQuota();
    },
  );
```

- In `render()`, in the `r.view === "fleet"` branch after `renderCards(...)`, add `watchHost(true); drawBand();`. In every other branch add `watchHost(false);` as the first statement, and in the two session branches add `drawSideQuota();` after `renderMeta(...)`.
- Add after `render()`:

```js
function watchHost(on) {
  if (on && !unsubHost) {
    unsubHost = conn.subscribe(
      "host",
      (snap) => {
        host = snap;
        drawBand();
      },
      (ops) => {
        for (const op of ops) if ("set" in op) host = op.set;
        drawBand();
      },
    );
  } else if (!on && unsubHost) {
    unsubHost();
    unsubHost = null;
    host = null;
  }
}

function drawBand() {
  if (root.dataset.view !== "fleet") return;
  renderBand($("band"), { metas: ordered, quota, host, server: conn.server, now: nowS() });
}

function drawSideQuota() {
  const p = quota.providers.find((x) => x.name === "claude");
  $("s-quota-sec").hidden = !p;
  if (!p) return;
  const now = nowS();
  const rows = p.state === "failed" ? [] : p.windows.map((w) => quotaSideRow(p, w, now));
  const foot = document.createElement("div");
  foot.className = "kv dim";
  const note = document.createElement("span");
  if (p.state === "ok") note.textContent = `read ${ago(p.read_at)}`; // "read just now", "read 2m ago"
  else {
    note.className = "gnote";
    note.textContent = p.state === "stale" ? `${p.note}, reading ${age(now - p.read_at)} old` : p.note;
  }
  foot.append(note);
  $("s-quota").replaceChildren(...rows, foot);
}

function drawQuota() {
  if (root.dataset.view === "fleet") drawBand();
  else if (root.dataset.view === "session") drawSideQuota();
}

// Countdowns and the tick move with the clock; their unit is minutes.
setInterval(drawQuota, 30 * 1000);
```

- [x] **Step 7: Add the CSS to `src/aegis/client/css/base.css`**

Append:

```css
/* gauges: the Fleet band and the sidebar's Quota section (js/gauges.js).
   Quota bars are --ok when calm, never --fill: in Ink --fill and --warn are
   the same amber, so a calm bar would read as a warning. */
#a2 .band{display:grid;grid-template-columns:minmax(170px,.55fr) 1fr 1.35fr;gap:28px;padding:16px 20px 14px;border-bottom:1px solid var(--rule);background:var(--side)}
#a2 .band h5{margin:0 0 8px;font-size:12px;font-weight:500;color:var(--faint);font-family:var(--font-head);display:flex;justify-content:space-between;gap:8px}
#a2 .band h5 .age{font-weight:400;font-family:var(--font-chrome);font-size:11px}
#a2 .band .counts{display:grid;gap:5px;font-size:12.5px;font-family:var(--font-chrome);color:var(--muted)}
#a2 .band .counts div{display:flex;align-items:center;gap:8px}
#a2 .band .counts b{color:var(--strong);font-weight:600;min-width:1.6ch;text-align:right}
#a2 .band .prov{font-size:11px;color:var(--faint);font-family:var(--font-chrome);margin:8px 0 2px}
#a2 .band .prov:first-child{margin-top:0}
@media (max-width:1100px){#a2 .band{grid-template-columns:1fr 1fr}#a2 .band .q{grid-column:1/-1}}
#a2 .gauge{display:grid;grid-template-columns:70px minmax(60px,1fr) 150px;gap:10px;align-items:center;font-size:12px;font-family:var(--font-chrome);color:var(--muted);margin:4px 0}
#a2 .gauge .bar{margin:0;position:relative}
#a2 .gauge .val{display:flex;gap:8px;justify-content:flex-end;white-space:nowrap}
#a2 .gauge .val b{font-weight:500;color:var(--ink)}
#a2 .gauge .val .rst{color:var(--faint)}
#a2 .gauge .note{grid-column:2/-1;color:var(--warn)}
#a2 .bar.q i{background:var(--ok)}
#a2 .bar.h i{background:var(--muted)}
#a2 .bar.warning i{background:var(--warn)}
#a2 .bar.critical i{background:var(--err)}
#a2 .warning .val b,#a2 .warning .val .proj,#a2 .sv.warning{color:var(--warn)}
#a2 .critical .val b,#a2 .critical .val .proj,#a2 .sv.critical{color:var(--err);font-weight:600}
#a2 .bar .tick{position:absolute;top:-2px;bottom:-2px;width:2px;margin-left:-1px;background:var(--strong);opacity:.7;border-radius:1px}
#a2 .stale .bar i{background:var(--faint)}
#a2 .stale .val b{color:var(--faint);font-weight:500}
#a2 .side .qrow{margin-bottom:9px}
#a2 .side .qrow .bar{position:relative;overflow:visible;margin:4px 0 0}
#a2 .side .qrow .bar .tick{top:-1px;bottom:-1px}
#a2 .side .gnote{color:var(--warn)}
```

- [x] **Step 8: Run the browser test**

Run: `uv run pytest -q tests/test_browser.py -k quota`
Expected: PASS.

- [x] **Step 9: Prove the test can fail**

```bash
cp src/aegis/client/js/gauges.js /tmp/gauges.js.orig
sed -i 's/const g = el("div", `gauge ${w.severity}${stale ? " stale" : ""}`);/const g = el("div", `gauge normal${stale ? " stale" : ""}`);/' src/aegis/client/js/gauges.js
cmp -s /tmp/gauges.js.orig src/aegis/client/js/gauges.js; echo "mutated (expect 1): $?"
uv run pytest -q tests/test_browser.py -k quota; echo "rc (expect 1): $?"
cp /tmp/gauges.js.orig src/aegis/client/js/gauges.js
cmp /tmp/gauges.js.orig src/aegis/client/js/gauges.js && echo restored
```

Expected: `mutated (expect 1): 1`, the test fails on the `critical` assertion with `rc (expect 1): 1`, then `restored`.

- [x] **Step 10: Run the whole browser suite and look at it**

Run: `uv run pytest -q -m browser` and expect every test to pass (the band must not break the existing Fleet tests).

Then take screenshots of the Fleet view and a session in each of the three themes, against a `quota_server`-style seeded server, and read them. The point is to check that the band matches the approved mockup: the columns line up, the tick shows, and the stale row is grey.

- [x] **Step 11: Commit**

```bash
git add src/aegis/client/js/gauges.js src/aegis/client/js/fleet.js src/aegis/client/js/app.js src/aegis/client/index.html src/aegis/client/css/base.css tests/test_browser.py
git commit -F - <<'EOF'
feat(client): quota and host gauges in the Fleet band and the sidebar (#146)

The Fleet view opens with a band: this server's sessions by state, CPU,
RAM, disk and average context, and every provider's quota windows with the
pace tick, the projection from 80% and the reset countdown. The session
sidebar gets a Quota section with Claude's two windows. A stale reading is
grey with its age; a provider with no reading is a note, not an empty bar.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 5: Docs, gates and the PR

**Files:**
- Create: `changelog.d/146-quota-gauges.added.md`
- Modify: `docs/superpowers/specs/2026-10-07-aegis-2-quota-gauges-design.md` (status line)
- Modify: `DESIGN.md` only if a rule that spans modules changed (expected: none; the cache rule lives in `aegis/quota/__init__.py`'s docstring)

- [x] **Step 1: Write the fragment**

`changelog.d/146-quota-gauges.added.md`:

```markdown
- **The quota and host gauges are back.** The Fleet view opens with a band:
  this server's sessions by state; CPU, RAM, disk and average context; and one
  gauge per window of every subscription aegis holds credentials for (Claude's
  5 hours and week, OpenCode Go's 5 hours, week and month). A tick on each bar
  marks how much of the window is gone, so fill past it means spending faster
  than the window refills; the projection at reset prints from 80%. The session
  sidebar gets a Quota section with Claude's two windows. Agents read the same
  numbers with `quota_read`, which never asks the vendor. Readings are shared
  with every aegis on the machine through `~/.cache/aegis/quota/`.
```

- [x] **Step 2: Flip the spec status**

Replace the spec's first status line with `**Status: implemented, 2026-10-07** (issue #146), following \`docs/superpowers/plans/2026-10-07-aegis-2-quota-gauges.md\`.` and keep the rest of that paragraph.

- [x] **Step 3: Run every gate**

```bash
make check
echo "check rc=$?"
make test-browser
echo "browser rc=$?"
make bench
echo "bench rc=$?"
```

Read each rc directly. All three must be 0. Keep the bench table for the PR body.

- [x] **Step 4: Commit, push, open the PR**

```bash
git add changelog.d/146-quota-gauges.added.md docs/superpowers/specs/2026-10-07-aegis-2-quota-gauges-design.md docs/superpowers/plans/2026-10-07-aegis-2-quota-gauges.md
git commit -F - <<'EOF'
docs: changelog fragment and spec status for the quota gauges (#146)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
git push -u origin feat/quota-gauges
gh pr create --title "feat: quota and host gauges in the Fleet band and the sidebar (#146)" --body-file /tmp/pr-146.md
```

The PR body (`/tmp/pr-146.md`) contains:
- what the PR does, closing #146;
- what was measured, including the bench table;
- what was tried and rejected: polling in the browser, a plugin, and `XDG_CACHE_HOME` in the tests;
- what was left out: cost analytics, a top-bar indicator, and the plugin move;
- the 🤖 attribution line.

Then wait on CI with an aegis monitor (`gh pr checks <n>` as the `done` condition).

---

### Task 6: Smoke test with `bin/aegis-dev`

- [ ] **Step 1: Start the PR's commit beside the released server**

```bash
mkdir -p /home/apiad/Workspace/.playground/aegis-quota-smoke
cp /home/apiad/Workspace/.aegis.yaml /home/apiad/Workspace/.playground/aegis-quota-smoke/
cd /home/apiad/Workspace && AEGIS_REF=feat/quota-gauges nohup bin/aegis-dev serve --window --port 8790 --root /home/apiad/Workspace/.playground/aegis-quota-smoke > /home/apiad/Workspace/.playground/aegis-quota-smoke/serve.log 2>&1 &
```

Check `serve.log` for the commit line `aegis-dev: feat/quota-gauges at <sha>` and confirm the sha equals the PR head (`gh pr view <n> --json headRefOid`).

- [ ] **Step 2: Hand it to Alex**

Tell Alex the window is open on :8790 at the PR's commit, what to look at (the band, a session's sidebar, hovering a gauge), and that quota readings are the real ones shared through `~/.cache/aegis/quota/`. Merge only after his smoke test and green CI.
