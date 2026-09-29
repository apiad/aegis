# F3 network block and tok/s — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give F3's SYSTEM block an exit IP, an egress-liveness reading with RTT, and an opt-in throughput figure; and put `⚡ N tok/s` back in the sidebar, along with the four other figures it silently discards.

**Architecture:** Three pure probe coroutines in a new `aegis.net` package, each returning a frozen dataclass where a network failure is a field rather than an exception. A `NetService` polls them on three different cadences (20s / 300s / off-by-default), modelled on `usage/quota.py:QuotaService`. The TUI's existing 1s `_tick` pushes the cached state to the active pane, which pre-renders it into tier tuples the way it already does for `system`, `clock`, `cwd` and `build`. Nothing in the render path touches the network or a live clock.

**Tech Stack:** Python 3.13+, `asyncio`, `httpx>=0.28` (already a dependency), Textual + Rich, `pytest` + `pytest-asyncio` + `pytest-httpx`, `uv`.

**Spec:** `docs/superpowers/specs/2026-09-27-f3-network-and-tps-design.md`

**Issues:** [#13](https://github.com/apiad/aegis/issues/13) (network block), [#12](https://github.com/apiad/aegis/issues/12) (tok/s regression).

## Global Constraints

- **Python 3.13 or newer. `uv`, never `pip`.** Run tests with `uv run pytest`.
- **Add no new dependency.** `httpx>=0.28` is already at `pyproject.toml:33`; `pytest-httpx>=0.36.2` is already in the `dev` group at `pyproject.toml:120`.
- **English in code, comments, identifiers, error strings, test names and commit messages.** Conventional commits, one logical change each.
- **This is a shared checkout.** Stage named paths only — `git add <path>`, never `-A`, `.` or `-u`. Never `git commit --amend`. Never `git stash`; use a throwaway commit.
- **Release notes are fragments.** Add a file to `changelog.d/<slug>.<category>.md`; never edit `CHANGELOG.md`. Categories: `added`, `changed`, `deprecated`, `removed`, `fixed`, `security`.
- **`rift check` must stay green.** Three of its rules bite this work: every config section documented, every slash command documented, every file path named in the docs exists. CI cannot run `rift` (it is private and not on PyPI), so run it locally before pushing.
- **No test touches the real network.** Every probe test uses a local `asyncio.start_server` or `pytest_httpx`. A test that passes because the internet happened to work is the exact failure this feature is about.
- **No test asserts a literal the code owns.** Bind thresholds, intervals and anchor defaults from `NetworkConfig` / the module constant, never restate them on both sides of an assertion.
- **Baseline:** `make test` on `origin/main` is 4702 passed, 45 skipped, rc=0 in ~96s. `make check` is red only on the advisory `ty` stage (356 diagnostics, issue #8) — that is pre-existing and not yours. Never read a gate's exit code through a pipe.

## Review Focus

Five conditions the spec implies and no task's happy path exercises, most likely to bite first. Each has a test in the task that owns the code.

1. **An IPv6 literal in `anchors`.** `"2606:4700:4700::1111:443".split(":")` yields six fields and a host of `"2606"`, which connects somewhere else or nowhere and reports it as a healthy anchor. Owned by Task 2 (`parse_anchor` splits from the right) and Task 4 (`_build_network` raises `ConfigError` on a malformed entry).
2. **A captive portal that completes the TCP handshake and answers 200 with an HTML login page.** This is the failure the whole block exists to catch, and the naive version reports it as healthy: `reach` succeeds, and a permissive `key=value` parser reads `<meta http-equiv="refresh" content="0;url=http://portal">` as a field and puts it on screen as an exit IP. Owned by Task 2 (`parse_trace` rejects non-alphanumeric keys, and `_valid_ip` requires the result to parse as an address).
3. **A throughput sample whose elapsed time rounds to zero.** `received / 0.0` raises, and `received / 0.0001` renders a triumphant meaningless figure. Owned by Task 2 (`MIN_ELAPSED_S` floor, reported as `too fast to measure`).
4. **A transfer truncated at 200 KB of 1 MB.** It computes a perfectly plausible rate and only the counts reveal it. Owned by Task 2 (`Throughput.complete`) and Task 5 (the row refuses an incomplete reading).
5. **`anchors: []` and `speed_bytes: 0` in config.** Both silently disable the feature at probe time, so the block renders forever as "not sampled yet" and nobody learns why. Owned by Task 4 (both raise `ConfigError` at load).

---

### Task 1: Put tok/s back in the sidebar

Independent of every other task — it fixes #12 and touches no network code. It goes first because it is the smallest shippable slice and it is where you learn the `_painted` test seam that Task 5 needs.

**Files:**
- Modify: `src/aegis/tui/sidebar.py` (the `SidebarModel` dataclass around `:80-90`, and `_context()` at `:283-327`)
- Modify: `src/aegis/tui/pane.py:3134-3169` (`_sidebar_model`)
- Test: `tests/test_sidebar_metrics.py` (create)
- Create: `changelog.d/12-sidebar-tok-s.fixed.md`

**Interfaces:**
- Consumes: `MetricsModel` fields that already exist — `c_out`, `p_out`, `c_in`, `p_in`, `c_cached`, `p_cached`, `c_think`, `tool_calls`, `tool_errors`, `compaction_count` (`src/aegis/tui/metrics.py:75-106`), and the method `recent_tps() -> float | None` (`:231`).
- Produces: `SidebarModel.tps: float | None`, `SidebarModel.cached_pct: int | None`, `SidebarModel.think_pct: int | None`, `SidebarModel.tools: tuple[int, int] | None`, `SidebarModel.compactions: int`. Nothing later in this plan depends on them.

**Background.** `MetricsModel.render_tiers()` puts the generation speed only in tier 0 (`metrics.py:344,351`). `_context()` selects `m.metrics[-1:]` — tier 3 — the moment `m.ctx` is set, which is every Claude session (`sidebar.py:315-319`). Tier 0 minus tier 3 is `⚡ N tok/s`, the cached share, the reasoning share, the tool count with its error count, and the compaction counter. The comment above that line justifies the choice for the context percentage, which the gauge genuinely replaces; it was never true of the other five.

- [ ] **Step 1: Write the failing test**

Create `tests/test_sidebar_metrics.py`:

```python
"""CONTEXT's per-turn figures, in a running app.

Assertions are on what the widget actually painted, not on the model field
being set: a model assertion is green against a section that was never
composed into `SECTIONS`. Same seam as `tests/test_sidebar_system.py`.
"""
from __future__ import annotations

import pytest

from aegis.config import Agent
from aegis.events import Result
from aegis.tui.app import AegisApp
from aegis.tui.sidebar import Sidebar


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


class FakeSession:
    session_id = "sid-1"

    def __init__(self):
        self.sent = []

    async def start(self): pass
    async def send(self, text): self.sent.append(text)

    async def events(self):
        yield Result(duration_ms=1, is_error=False)

    async def close(self): pass


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"

    def bind(self, bridge): self.bound = bridge
    async def start(self): pass
    async def stop(self): pass


def _app():
    def make(agent, mcp_url, handle, **kw):
        return FakeSession()
    return AegisApp({"default": _agent()}, "default", make, FakeMCP())


def _painted(pane) -> str:
    sidebar = pane.query_one(Sidebar)
    assert sidebar._paints > 0, "the sidebar never painted"
    return sidebar.plain()


def _generated_a_turn(core, *, out: int, seconds: float) -> None:
    """Drive the metrics model the way a finished generation turn does.

    `recent_tps` samples only turns with positive output over a measured
    duration (`metrics.py:228-230`), so both have to be real.
    """
    m = core.metrics
    m._turn_rates.append((out, seconds))
    m.c_out += out
    m.c_in += out * 10
    m.c_cached += out * 8
    m.c_think += out // 2
    m.tool_calls += 3
    m.tool_errors += 1
    m.compaction_count += 1
    m.last_true_input = out * 10
    m.context_window = out * 100


@pytest.mark.asyncio
async def test_the_sidebar_shows_generation_speed_beside_its_own_gauge():
    """The regression in #12: drawing the CTX bar cost the row five figures.

    `context_window` is set, so `MetricsModel.gauge()` returns a reading and
    `_context` takes the branch that used to fall back to tier 3.
    """
    app = _app()
    async with app.run_test(size=(120, 40)) as pilot:
        pane = app._panes[0]
        _generated_a_turn(pane._core, out=600, seconds=6.0)
        pane.toggle_task_dock()
        await pilot.pause()

        painted = _painted(pane)
        assert pane._core.metrics.gauge() is not None, "no gauge, wrong branch"
        assert "tok/s" in painted
        # 600 output tokens over 6.0s. Derived from the same inputs the helper
        # fed in rather than written as a literal, so a changed helper cannot
        # leave a stale expectation passing.
        assert f"{round(600 / 6.0)} tok/s" in painted


@pytest.mark.asyncio
async def test_the_sidebar_keeps_the_four_figures_tier_three_drops():
    app = _app()
    async with app.run_test(size=(120, 40)) as pilot:
        pane = app._panes[0]
        _generated_a_turn(pane._core, out=600, seconds=6.0)
        pane.toggle_task_dock()
        await pilot.pause()

        painted = _painted(pane)
        assert "cached" in painted
        assert "think" in painted
        assert "⚒" in painted
        assert "✂" in painted


@pytest.mark.asyncio
async def test_a_session_with_no_completed_turn_shows_no_speed_row():
    """`recent_tps()` is None before any generation turn, and a row that
    renders `0 tok/s` there would claim a measurement nobody made."""
    app = _app()
    async with app.run_test(size=(120, 40)) as pilot:
        pane = app._panes[0]
        pane.toggle_task_dock()
        await pilot.pause()
        assert "tok/s" not in _painted(pane)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_sidebar_metrics.py -v`

Expected: the first two FAIL (no `tok/s`, no `cached` in the painted output); the third PASSES already. A third-test failure here means something else is wrong — stop and investigate before continuing.

- [ ] **Step 3: Add the scalars to `SidebarModel`**

In `src/aegis/tui/sidebar.py`, inside the `# CONTEXT` block of `SidebarModel` (after `quota_gauges`), add:

```python
    # The per-turn figures tier 3 throws away. Carried as numbers rather than
    # scavenged out of the rendered tier string: `_context` draws its own CTX
    # gauge and so selects the narrowest tier, which drops all five of these
    # even on an 80-cell column (#12). The `metrics` tuple above stays — a
    # remote pane is handed rendered strings and no numbers, and it remains
    # the fallback for exactly that case.
    tps: float | None = None
    cached_pct: int | None = None
    think_pct: int | None = None
    tools: tuple[int, int] | None = None  # (calls, errors)
    compactions: int = 0
```

- [ ] **Step 4: Render them in `_context()`**

In `src/aegis/tui/sidebar.py`, inside `_context()`, immediately **before** the existing `if m.metrics:` block at `:315`, insert:

```python
    # Everything the CTX gauge does not draw. Ordered by how often it changes:
    # speed moves every turn, the shares move with the turn's shape, the
    # counters only accumulate.
    facts: list[str] = []
    if m.tps is not None:
        facts.append(f"[{palette.accent}]⚡ {round(m.tps)} tok/s[/]")
    if m.cached_pct is not None:
        facts.append(f"[{palette.muted}]{m.cached_pct}% cached[/]")
    if m.think_pct is not None:
        facts.append(f"[{palette.muted}]{m.think_pct}% think[/]")
    if m.tools is not None:
        calls, errors = m.tools
        tool = f"⚒ {calls}"
        if errors:
            tool = f"{tool} [{palette.error}]({errors} err)[/]"
        facts.append(tool)
    if m.compactions:
        cut_style = palette.error if m.compactions >= 2 else palette.working
        facts.append(f"[{cut_style}]✂{m.compactions}[/]")
    if facts:
        # Tiers, widest first: `fit_rows` takes the widest that fits and drops
        # a segment whose narrowest still overflows, so the speed — the one
        # figure with no other surface — is in every tier.
        rows += _rows(
            [
                Segment(
                    "turn_facts",
                    tuple(
                        dict.fromkeys(
                            (
                                " · ".join(facts),
                                " · ".join(facts[:2]),
                                facts[0],
                            )
                        )
                    ),
                    0,
                )
            ],
            palette,
            width,
        )
```

- [ ] **Step 5: Fill the scalars in `pane._sidebar_model()`**

In `src/aegis/tui/pane.py`, inside the `return SidebarModel(` call at `:3134`, after the existing `quota_gauges=self._quota_gauges,` line, add:

```python
            tps=core.metrics.recent_tps(),
            cached_pct=_share(
                core.metrics.c_cached + core.metrics.p_cached,
                core.metrics.c_in + core.metrics.p_in,
            ),
            think_pct=_share(
                core.metrics.c_think, core.metrics.c_out + core.metrics.p_out
            ),
            tools=(core.metrics.tool_calls, core.metrics.tool_errors)
            if core.metrics.tool_calls
            else None,
            compactions=core.metrics.compaction_count,
```

And add this module-level helper to `src/aegis/tui/pane.py`, immediately above the `ConversationPane` class definition:

```python
def _share(part: int, whole: int) -> int | None:
    """``part`` as a whole-number percentage of ``whole``, or None.

    None rather than 0 when there is no denominator: a zero share claims a
    measured ratio of nothing, which is the same mistake `_context` avoids by
    falling back to a tier instead of drawing a 0% bar.
    """
    if whole <= 0 or part <= 0:
        return None
    return round(100 * part / whole)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_sidebar_metrics.py -v`

Expected: all three PASS.

- [ ] **Step 7: Prove the first test can fail**

A green assertion is worth nothing until you have seen it go red for the right reason.

```bash
cp src/aegis/tui/sidebar.py /tmp/sidebar.orig
sed -i 's/if m.tps is not None:/if False:/' src/aegis/tui/sidebar.py
cmp /tmp/sidebar.orig src/aegis/tui/sidebar.py && echo "MUTATION DID NOT APPLY — stop" || echo "mutation applied"
uv run pytest tests/test_sidebar_metrics.py -v
```

Expected: `mutation applied`, then `test_the_sidebar_shows_generation_speed_beside_its_own_gauge` FAILS. Then restore:

```bash
cp /tmp/sidebar.orig src/aegis/tui/sidebar.py
uv run pytest tests/test_sidebar_metrics.py -q
```

Expected: all three PASS again.

- [ ] **Step 8: Run the fast lane**

Run: `make test`

Expected: rc=0, 4705 passed (the baseline 4702 plus these three), 45 skipped. Read the exit code directly — never through a pipe.

- [ ] **Step 9: Write the changelog fragment**

```bash
cat > changelog.d/12-sidebar-tok-s.fixed.md <<'EOF'
- **The F3 sidebar shows `⚡ N tok/s` again, along with four other figures it
  had been discarding.** Drawing its own CTX gauge made the CONTEXT section
  fall back to the narrowest metrics tier, which carries neither the
  generation speed nor the cached share, the reasoning share, the tool count
  or the compaction counter — so the panel that exists to spend the vertical
  axis on detail was showing less of it than the one-line status bar. It now
  reads the numbers off the metrics model instead of scavenging a rendered
  string.
EOF
make changelog-check
```

Expected: rc=0.

- [ ] **Step 10: Commit**

```bash
git add src/aegis/tui/sidebar.py src/aegis/tui/pane.py \
        tests/test_sidebar_metrics.py changelog.d/12-sidebar-tok-s.fixed.md
git commit -F - <<'EOF'
fix(sidebar): tok/s and four other figures, lost to the CTX gauge

`_context` selected the narrowest metrics tier whenever it had a gauge to
draw, and only tier 0 carries the generation speed. Tier 0 minus tier 3 is
tok/s, the cached share, the reasoning share, the tool count with its error
count, and the compaction counter — five figures, not one.

Read as scalars off MetricsModel rather than parsed back out of a rendered
string. The tier tuple stays: a remote pane is handed strings and no
numbers, and it is still the fallback for that case.

Closes #12

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 2: The probes

**Files:**
- Create: `src/aegis/net/__init__.py`
- Create: `src/aegis/net/probe.py`
- Test: `tests/test_net_probe.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks. `httpx` and stdlib only. This module imports nothing from `aegis`, which is what lets `aegis.config` call `parse_anchor` without a cycle.
- Produces:
  - `parse_anchor(text: str) -> tuple[str, int]`
  - `parse_trace(text: str) -> dict[str, str]`
  - `async reach(anchors: tuple[tuple[str, int], ...] = DEFAULT_ANCHORS, timeout: float = 3.0) -> Reach`
  - `async trace(url: str = TRACE_URL, fallback_url: str = TRACE_FALLBACK_URL, timeout: float = 5.0) -> Trace`
  - `async throughput(nbytes: int = 1_000_000, url: str = SPEED_URL, timeout: float = 30.0) -> Throughput`
  - `Reach(ok: bool, rtt_ms: float, anchor: str, error: str, per_anchor: tuple[tuple[str, float | None], ...])`
  - `Trace(ok: bool, ip: str, colo: str, loc: str, error: str)`
  - `Throughput(ok: bool, bytes_per_s: float, received: int, asked: int, elapsed_s: float, error: str)` with a `complete` property
  - Constants `DEFAULT_ANCHORS`, `TRACE_URL`, `TRACE_FALLBACK_URL`, `SPEED_URL`, `MIN_ELAPSED_S`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_net_probe.py`:

```python
"""The three network probes.

Nothing here touches the real network: `reach` runs against a local
`asyncio.start_server`, and the two HTTP probes run on `pytest_httpx`. A
probe test that passes because the internet happened to work is precisely
the failure this feature exists to report.
"""
from __future__ import annotations

import asyncio

import pytest

from aegis.net.probe import (
    DEFAULT_ANCHORS,
    MIN_ELAPSED_S,
    SPEED_URL,
    TRACE_FALLBACK_URL,
    TRACE_URL,
    Reach,
    parse_anchor,
    parse_trace,
    reach,
    throughput,
    trace,
)


# ----- parse_anchor -----

def test_parse_anchor_reads_host_and_port():
    assert parse_anchor("1.1.1.1:443") == ("1.1.1.1", 443)


def test_parse_anchor_survives_an_ipv6_literal():
    """Review Focus 1. Splitting on ':' left-to-right gives a host of '2606'
    and a probe that reports a healthy anchor somewhere else entirely."""
    assert parse_anchor("2606:4700:4700::1111:443") == (
        "2606:4700:4700::1111", 443,
    )


def test_parse_anchor_strips_the_brackets_an_ipv6_literal_may_carry():
    assert parse_anchor("[2606:4700:4700::1111]:443") == (
        "2606:4700:4700::1111", 443,
    )


def test_parse_anchor_refuses_a_bare_host():
    with pytest.raises(ValueError):
        parse_anchor("1.1.1.1")


def test_parse_anchor_refuses_a_non_numeric_port():
    with pytest.raises(ValueError):
        parse_anchor("1.1.1.1:https")


def test_the_default_anchors_are_two_different_operators():
    """One provider having a bad day must not read as 'no egress'."""
    hosts = {h for h, _ in DEFAULT_ANCHORS}
    assert len(hosts) >= 2


# ----- reach -----

@pytest.mark.asyncio
async def test_reach_reports_a_listening_port_with_an_rtt():
    server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        got = await reach((("127.0.0.1", port),), timeout=2.0)
    finally:
        server.close()
        await server.wait_closed()
    assert got.ok
    assert got.rtt_ms >= 0.0
    assert got.anchor == f"127.0.0.1:{port}"
    assert got.per_anchor == ((f"127.0.0.1:{port}", got.rtt_ms),)


@pytest.mark.asyncio
async def test_reach_reports_a_closed_port_as_no_egress():
    # Bind, read the port, close it: nothing else can be listening there.
    server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()

    got = await reach((("127.0.0.1", port),), timeout=2.0)
    assert not got.ok
    assert got.error
    assert got.per_anchor == ((f"127.0.0.1:{port}", None),)


@pytest.mark.asyncio
async def test_reach_probes_every_anchor_so_net_can_print_each():
    """One dead and one live anchor: ok overall, and both readings kept."""
    server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    live = server.sockets[0].getsockname()[1]
    dead_server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    dead = dead_server.sockets[0].getsockname()[1]
    dead_server.close()
    await dead_server.wait_closed()
    try:
        got = await reach((("127.0.0.1", dead), ("127.0.0.1", live)), timeout=2.0)
    finally:
        server.close()
        await server.wait_closed()

    assert got.ok
    assert got.anchor == f"127.0.0.1:{live}"
    assert dict(got.per_anchor)[f"127.0.0.1:{dead}"] is None
    assert dict(got.per_anchor)[f"127.0.0.1:{live}"] is not None


@pytest.mark.asyncio
async def test_reach_with_no_anchors_is_a_failure_not_a_success():
    """Review Focus 5's probe-side half: an empty list must not read as ok."""
    got = await reach((), timeout=2.0)
    assert not got.ok
    assert got.error


# ----- parse_trace -----

def test_parse_trace_reads_the_fields_cloudflare_returns():
    body = "fl=368f137\nh=www.cloudflare.com\nip=1.2.3.4\ncolo=MIA\nloc=US\n"
    got = parse_trace(body)
    assert got["ip"] == "1.2.3.4"
    assert got["colo"] == "MIA"
    assert got["loc"] == "US"


def test_parse_trace_ignores_a_captive_portals_html():
    """Review Focus 2. A portal answers 200 with a login page; a permissive
    key=value parser turns its meta-refresh into a field, and the sidebar
    then prints markup where an exit IP belongs."""
    body = (
        '<!DOCTYPE html>\n<html><head>\n'
        '<meta http-equiv="refresh" content="0;url=http://portal.example/login">\n'
        "</head><body>Sign in</body></html>\n"
    )
    assert parse_trace(body) == {}


# ----- trace -----

@pytest.mark.asyncio
async def test_trace_returns_the_exit_ip_colo_and_location(httpx_mock):
    httpx_mock.add_response(
        url=TRACE_URL, text="ip=2a0d:5600:6:202::15\ncolo=MIA\nloc=US\n"
    )
    got = await trace()
    assert got.ok
    assert got.ip == "2a0d:5600:6:202::15"
    assert got.colo == "MIA"
    assert got.loc == "US"


@pytest.mark.asyncio
async def test_trace_falls_back_when_the_trace_host_does_not_answer(httpx_mock):
    httpx_mock.add_exception(Exception("boom"), url=TRACE_URL)
    httpx_mock.add_response(url=TRACE_FALLBACK_URL, text="203.0.113.7\n")
    got = await trace()
    assert got.ok
    assert got.ip == "203.0.113.7"
    # The fallback carries an address and nothing else; the renderer simply
    # has no colo to draw.
    assert got.colo == ""


@pytest.mark.asyncio
async def test_trace_refuses_a_value_that_is_not_an_address(httpx_mock):
    """Review Focus 2, the second half: parsing something is not the same as
    parsing an IP. Whatever comes back has to be an address or it is not one."""
    httpx_mock.add_response(url=TRACE_URL, text="ip=sign-in-required\n")
    httpx_mock.add_response(url=TRACE_FALLBACK_URL, text="<html>portal</html>")
    got = await trace()
    assert not got.ok
    assert got.ip == ""


# ----- throughput -----

@pytest.mark.asyncio
async def test_throughput_measures_a_complete_transfer(httpx_mock):
    asked = 4096
    httpx_mock.add_response(url=SPEED_URL.format(bytes=asked), content=b"x" * asked)
    got = await throughput(nbytes=asked)
    assert got.ok
    assert got.complete
    assert got.received == asked
    assert got.asked == asked
    assert got.bytes_per_s > 0


@pytest.mark.asyncio
async def test_throughput_reports_a_truncated_transfer_as_incomplete(httpx_mock):
    """Review Focus 4. 200 KB of 1 MB computes a plausible rate; only the
    counts reveal it, so `complete` is what the renderer asks."""
    asked, short = 1_000_000, 200_000
    httpx_mock.add_response(url=SPEED_URL.format(bytes=asked), content=b"x" * short)
    got = await throughput(nbytes=asked)
    assert got.received == short
    assert got.asked == asked
    assert not got.complete


@pytest.mark.asyncio
async def test_throughput_refuses_a_transfer_too_fast_to_measure(
    httpx_mock, monkeypatch
):
    """Review Focus 3. `received / 0.0` raises and `received / 1e-9` renders a
    triumphant meaningless figure, so a sample under the floor is not a sample.

    A clock that does not advance rather than a small body and a hope: at
    4 KB over loopback the real elapsed time is genuinely near the floor, so
    a timing-based version of this test would pass or fail with the load on
    the machine. Patched on the module's own `time` reference, not on the
    stdlib module, so nothing else in the process is affected.
    """
    from types import SimpleNamespace

    import aegis.net.probe as probe

    asked = 4096
    httpx_mock.add_response(
        url=probe.SPEED_URL.format(bytes=asked), content=b"x" * asked
    )
    monkeypatch.setattr(probe, "time", SimpleNamespace(monotonic=lambda: 100.0))

    got = await probe.throughput(nbytes=asked)
    assert not got.ok
    # Floor read off the module, never restated as a literal here.
    assert got.elapsed_s < MIN_ELAPSED_S
    assert "too fast" in got.error
    assert got.bytes_per_s == 0.0
    # The counts survive a refusal — `/net` prints them to explain itself.
    assert got.received == asked


@pytest.mark.asyncio
async def test_throughput_refuses_a_non_positive_byte_count():
    """Review Focus 5's probe-side half for speed_bytes."""
    got = await throughput(nbytes=0)
    assert not got.ok
    assert got.error


@pytest.mark.asyncio
async def test_throughput_reports_a_dead_host_without_raising(httpx_mock):
    asked = 4096
    httpx_mock.add_exception(Exception("no route"), url=SPEED_URL.format(bytes=asked))
    got = await throughput(nbytes=asked)
    assert not got.ok
    assert got.error
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_net_probe.py -v`

Expected: collection ERROR — `ModuleNotFoundError: No module named 'aegis.net'`.

- [ ] **Step 3: Create the package and the probes**

Create `src/aegis/net/__init__.py`:

```python
"""Network probes and the service that caches them.

Three readings that differ by a factor of three thousand in cost — a TCP
handshake, a 300-byte lookup, a megabyte download — so they cannot share a
cadence. `probe` holds the pure functions; `service` holds the one that
polls them and the state the UI reads.
"""
```

Create `src/aegis/net/probe.py`:

```python
"""Three network readings, each a value rather than an exception.

A probe that raises makes every caller write the same `try`, and one of them
forgets; worse, in a 1-second UI tick already wrapped in
`contextlib.suppress`, a raise is indistinguishable from "no reading" and
the screen says nothing. So a network failure is a field.

Nothing here imports from `aegis`. That is deliberate: `aegis.config` calls
`parse_anchor` to validate at load time, and a dependency the other way
would be a cycle.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import time
from dataclasses import dataclass

# Two operators, so one having a bad day does not read as "no egress".
DEFAULT_ANCHORS: tuple[tuple[str, int], ...] = (("1.1.1.1", 443), ("8.8.8.8", 443))

# One ~300-byte response carries the exit IP, the Cloudflare colo and the
# country. `ifconfig.me` carries the address alone, so it is the fallback
# rather than the primary.
TRACE_URL = "https://www.cloudflare.com/cdn-cgi/trace"
TRACE_FALLBACK_URL = "https://ifconfig.me/ip"
SPEED_URL = "https://speed.cloudflare.com/__down?bytes={bytes}"

# Below this, a transfer has not been measured: dividing by it yields a
# number with no upper bound, which renders as a triumphant meaningless
# figure rather than as the absence of a reading.
MIN_ELAPSED_S = 0.01


@dataclass(frozen=True)
class Reach:
    """Whether packets leave, and how long the handshake took."""

    ok: bool
    rtt_ms: float = 0.0
    anchor: str = ""  # which anchor supplied `rtt_ms`
    error: str = ""
    # Every anchor's reading, `None` where it did not connect. `/net` prints
    # all of them; the sidebar row has room for the best one.
    per_anchor: tuple[tuple[str, float | None], ...] = ()


@dataclass(frozen=True)
class Trace:
    ok: bool
    ip: str = ""
    colo: str = ""
    loc: str = ""
    error: str = ""


@dataclass(frozen=True)
class Throughput:
    ok: bool
    bytes_per_s: float = 0.0
    received: int = 0
    asked: int = 0
    elapsed_s: float = 0.0
    error: str = ""

    @property
    def complete(self) -> bool:
        """Whether the transfer delivered what it was asked for.

        A stream that dies at 200 KB of a megabyte computes a perfectly
        plausible rate, and only the counts reveal it — so the renderer asks
        this rather than trusting `ok`.
        """
        return self.ok and self.asked > 0 and self.received >= self.asked


def parse_anchor(text: str) -> tuple[str, int]:
    """`"1.1.1.1:443"` or `"[2606:4700::1111]:443"` -> `(host, port)`.

    Partitioned from the RIGHT, and brackets stripped. A left-to-right split
    of `"2606:4700:4700::1111:443"` yields six fields and a host of `"2606"`,
    which connects somewhere else or nowhere and reports it as a healthy
    anchor — a probe lying in the one direction that matters.
    """
    host, sep, port = text.rpartition(":")
    if not sep or not host:
        raise ValueError(f"anchor must be host:port, got {text!r}")
    try:
        number = int(port)
    except ValueError:
        raise ValueError(f"anchor port must be a number, got {port!r}") from None
    if not 0 < number < 65536:
        raise ValueError(f"anchor port out of range: {number}")
    return host.strip("[]"), number


def parse_trace(text: str) -> dict[str, str]:
    """`cdn-cgi/trace`'s `key=value` lines as a dict.

    A line counts only when its key is short and alphanumeric. A captive
    portal answers 200 with an HTML login page, and a permissive parser reads
    `<meta http-equiv="refresh" content="0;url=...">` as a field — which then
    arrives on screen where an exit IP belongs.
    """
    out: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if not sep or not key.isalnum() or len(key) > 12:
            continue
        out[key] = value.strip()
    return out


def _is_address(text: str) -> bool:
    """Whether `text` parses as an IPv4 or IPv6 address.

    Parsing *something* out of a response is not the same as parsing an IP.
    This is the guard that keeps `sign-in-required` off the exit-IP row.
    """
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


async def _connect(host: str, port: int, timeout: float) -> float | None:
    """Handshake RTT in milliseconds, or `None` if it did not connect."""
    started = time.monotonic()
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout
        )
    except (OSError, asyncio.TimeoutError):
        return None
    rtt = (time.monotonic() - started) * 1000.0
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    return rtt


async def reach(
    anchors: tuple[tuple[str, int], ...] = DEFAULT_ANCHORS, timeout: float = 3.0
) -> Reach:
    """Egress liveness and handshake RTT.

    TCP rather than ICMP: `ping` needs `CAP_NET_RAW` or a subprocess per
    sample, and a captive portal — the failure this exists to catch — answers
    ICMP and refuses the connection.

    Every anchor is probed concurrently rather than in sequence. Two
    handshakes in parallel cost the same wall-clock as one, and it is what
    lets `/net` print a reading per anchor instead of only the winner's.
    """
    if not anchors:
        return Reach(ok=False, error="no anchors configured")
    labels = [f"{host}:{port}" for host, port in anchors]
    rtts = await asyncio.gather(
        *(_connect(host, port, timeout) for host, port in anchors)
    )
    per_anchor = tuple(zip(labels, rtts, strict=True))
    live = [(label, rtt) for label, rtt in per_anchor if rtt is not None]
    if not live:
        return Reach(ok=False, error="unreachable", per_anchor=per_anchor)
    label, best = min(live, key=lambda pair: pair[1])
    return Reach(ok=True, rtt_ms=best, anchor=label, per_anchor=per_anchor)


async def trace(
    url: str = TRACE_URL,
    fallback_url: str = TRACE_FALLBACK_URL,
    timeout: float = 5.0,
) -> Trace:
    """The exit IP, plus the colo and country when the primary answers."""
    import httpx

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
            fields = parse_trace(response.text)
            found = fields.get("ip", "")
            if _is_address(found):
                return Trace(
                    ok=True,
                    ip=found,
                    colo=fields.get("colo", ""),
                    loc=fields.get("loc", ""),
                )
        except Exception:  # noqa: BLE001 — httpx raises a family, plus parse errors
            pass
        try:
            response = await client.get(fallback_url)
            response.raise_for_status()
            found = response.text.strip()
            if _is_address(found):
                return Trace(ok=True, ip=found)
        except Exception:  # noqa: BLE001
            pass
    return Trace(ok=False, error="no exit ip")


async def throughput(
    nbytes: int = 1_000_000, url: str = SPEED_URL, timeout: float = 30.0
) -> Throughput:
    """A timed download, reported with the inputs it was computed from."""
    import httpx

    if nbytes <= 0:
        return Throughput(ok=False, asked=nbytes, error="byte count must be positive")
    target = url.format(bytes=nbytes)
    received = 0
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("GET", target) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    received += len(chunk)
    except Exception as exc:  # noqa: BLE001
        return Throughput(
            ok=False,
            received=received,
            asked=nbytes,
            elapsed_s=time.monotonic() - started,
            error=type(exc).__name__,
        )
    elapsed = time.monotonic() - started
    if elapsed < MIN_ELAPSED_S:
        return Throughput(
            ok=False,
            received=received,
            asked=nbytes,
            elapsed_s=elapsed,
            error="too fast to measure",
        )
    return Throughput(
        ok=True,
        bytes_per_s=received / elapsed,
        received=received,
        asked=nbytes,
        elapsed_s=elapsed,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_net_probe.py -v`

Expected: all PASS.

- [ ] **Step 5: Prove the IPv6 anchor test can fail**

```bash
cp src/aegis/net/probe.py /tmp/probe.orig
sed -i 's/host, sep, port = text.rpartition(":")/host, sep, port = text.partition(":")/' src/aegis/net/probe.py
cmp /tmp/probe.orig src/aegis/net/probe.py && echo "MUTATION DID NOT APPLY — stop" || echo "mutation applied"
uv run pytest tests/test_net_probe.py -v -k ipv6
```

Expected: `mutation applied`, then `test_parse_anchor_survives_an_ipv6_literal` FAILS. Restore:

```bash
cp /tmp/probe.orig src/aegis/net/probe.py
uv run pytest tests/test_net_probe.py -q
```

Expected: all PASS again.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/net/__init__.py src/aegis/net/probe.py tests/test_net_probe.py
git commit -F - <<'EOF'
feat(net): three probes — egress, exit IP, throughput

Each returns a frozen dataclass where a network failure is a field. A probe
that raises is indistinguishable from "no reading" inside the 1s tick, which
is already wrapped in contextlib.suppress, and the screen would say nothing.

Three details that are the point rather than defensiveness. parse_anchor
partitions from the right, because a left-to-right split of an IPv6 literal
yields a host of "2606" and reports a healthy anchor somewhere else.
parse_trace takes only short alphanumeric keys and the result must parse as
an address, because a captive portal answers 200 with HTML and a permissive
parser puts its meta-refresh on the exit-IP row. throughput reports the byte
count and elapsed time it divided, because a stream that dies at 200 KB of a
megabyte computes a perfectly plausible rate.

Refs #13

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 3: `NetService`

**Files:**
- Create: `src/aegis/net/service.py`
- Test: `tests/test_net_service.py` (create)

**Interfaces:**
- Consumes: `Reach`, `Trace`, `Throughput` from Task 2 (`aegis.net.probe`).
- Produces:
  - `NetState(reach, reach_at, trace, trace_at, speed, speed_at)`, frozen, with a `sampled` property
  - `NetService(cfg, *, probes=None, clock=time.monotonic)` with `state` and `started` properties, `start()`, `async stop()`, `async refresh(*, force_speed: bool = False)`
  - `Probes` — a `NamedTuple` of the three callables, so a test injects three fakes in one object
- **`cfg` duck-types on the `NetworkConfig` Task 4 adds.** This task defines no config class; write the tests against a small local stand-in with the same attribute names (`enabled`, `interval`, `trace_interval`, `speed_interval`, `speed_bytes`, `timeout`, `anchors`). Task 4 then supplies the real one and adds the test that they agree.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_net_service.py`:

```python
"""NetService — three cadences over one task.

Probes are injected and the clock is fake, so nothing here waits and nothing
touches a socket. The behaviours under test are exactly the ones a live
network would make hard to reproduce: the forced re-trace when egress comes
back, and what survives a failure.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from aegis.net.probe import Reach, Throughput, Trace
from aegis.net.service import NetService, Probes


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


@dataclass
class Cfg:
    """Stand-in for the NetworkConfig Task 4 adds — same attribute names."""

    enabled: bool = True
    interval: float = 20.0
    trace_interval: float = 300.0
    speed_interval: float = 0.0
    speed_bytes: int = 1_000_000
    timeout: float = 3.0
    anchors: tuple[tuple[str, int], ...] = (("1.1.1.1", 443),)


@dataclass
class Spy:
    """Scripted probes that record every call."""

    reaches: list = field(default_factory=list)
    traces: list = field(default_factory=list)
    speeds: list = field(default_factory=list)
    reach_calls: int = 0
    trace_calls: int = 0
    speed_calls: int = 0

    def probes(self) -> Probes:
        async def reach(anchors, timeout):
            self.reach_calls += 1
            return self.reaches.pop(0) if self.reaches else Reach(ok=True, rtt_ms=10.0)

        async def trace(timeout):
            self.trace_calls += 1
            return self.traces.pop(0) if self.traces else Trace(ok=True, ip="1.2.3.4")

        async def throughput(nbytes, timeout):
            self.speed_calls += 1
            return (
                self.speeds.pop(0)
                if self.speeds
                else Throughput(ok=True, bytes_per_s=1e5, received=nbytes,
                                asked=nbytes, elapsed_s=10.0)
            )

        return Probes(reach=reach, trace=trace, throughput=throughput)


def _service(clock, spy, cfg=None):
    return NetService(cfg or Cfg(), probes=spy.probes(), clock=clock)


@pytest.mark.asyncio
async def test_the_first_refresh_samples_egress_and_the_exit_ip():
    clock, spy = Clock(), Spy()
    svc = _service(clock, spy)
    await svc.refresh()

    assert svc.state.sampled
    assert svc.state.reach.ok
    assert svc.state.trace.ip == "1.2.3.4"
    assert spy.reach_calls == 1
    assert spy.trace_calls == 1


@pytest.mark.asyncio
async def test_the_exit_ip_is_not_re_read_on_every_tick():
    """It costs 300 bytes and changes almost never; RTT is the cheap one."""
    clock, spy = Clock(), Spy()
    cfg = Cfg()
    svc = _service(clock, spy, cfg)
    await svc.refresh()
    clock.advance(cfg.interval)
    await svc.refresh()

    assert spy.reach_calls == 2
    assert spy.trace_calls == 1


@pytest.mark.asyncio
async def test_the_exit_ip_is_re_read_once_its_own_cadence_is_due():
    clock, spy = Clock(), Spy()
    cfg = Cfg()
    svc = _service(clock, spy, cfg)
    await svc.refresh()
    clock.advance(cfg.trace_interval)
    await svc.refresh()

    assert spy.trace_calls == 2


@pytest.mark.asyncio
async def test_egress_coming_back_forces_a_re_read_of_the_exit_ip():
    """The whole reason the transition is tracked: an address changes at
    exactly one moment, and it is this one. Polling for it is waste."""
    clock, spy = Clock(), Spy()
    spy.reaches = [
        Reach(ok=True, rtt_ms=10.0),
        Reach(ok=False, error="unreachable"),
        Reach(ok=True, rtt_ms=11.0),
    ]
    spy.traces = [Trace(ok=True, ip="1.2.3.4"), Trace(ok=True, ip="5.6.7.8")]
    svc = _service(clock, spy)

    await svc.refresh()                      # up, traces
    clock.advance(1.0)
    await svc.refresh()                      # down
    clock.advance(1.0)
    await svc.refresh()                      # back up — forces a trace

    assert spy.trace_calls == 2, "the transition did not force a re-read"
    assert svc.state.trace.ip == "5.6.7.8"


@pytest.mark.asyncio
async def test_the_transition_fires_once_not_on_every_later_tick():
    clock, spy = Clock(), Spy()
    spy.reaches = [
        Reach(ok=False, error="unreachable"),
        Reach(ok=True, rtt_ms=10.0),
        Reach(ok=True, rtt_ms=10.0),
    ]
    svc = _service(clock, spy)
    await svc.refresh()
    clock.advance(1.0)
    await svc.refresh()
    before = spy.trace_calls
    clock.advance(1.0)
    await svc.refresh()

    assert spy.trace_calls == before


@pytest.mark.asyncio
async def test_a_failed_reach_does_not_erase_the_last_known_exit_ip():
    """Losing egress says nothing about what the address was, and the row it
    would blank is the one that tells you which network you fell onto."""
    clock, spy = Clock(), Spy()
    spy.reaches = [Reach(ok=True, rtt_ms=10.0), Reach(ok=False, error="unreachable")]
    svc = _service(clock, spy)
    await svc.refresh()
    clock.advance(1.0)
    await svc.refresh()

    assert not svc.state.reach.ok
    assert svc.state.trace.ip == "1.2.3.4"


@pytest.mark.asyncio
async def test_a_failed_trace_does_not_erase_the_last_known_exit_ip():
    clock, spy = Clock(), Spy()
    cfg = Cfg()
    spy.traces = [Trace(ok=True, ip="1.2.3.4"), Trace(ok=False, error="no exit ip")]
    svc = _service(clock, spy, cfg)
    await svc.refresh()
    clock.advance(cfg.trace_interval)
    await svc.refresh()

    assert svc.state.trace.ip == "1.2.3.4"


@pytest.mark.asyncio
async def test_nothing_is_probed_beyond_reach_while_egress_is_down():
    """No point spending a lookup or a megabyte on a link that just failed."""
    clock, spy = Clock(), Spy()
    spy.reaches = [Reach(ok=False, error="unreachable")]
    svc = _service(clock, spy, Cfg(speed_interval=60.0))
    await svc.refresh()

    assert spy.reach_calls == 1
    assert spy.trace_calls == 0
    assert spy.speed_calls == 0


@pytest.mark.asyncio
async def test_the_speed_probe_stays_silent_when_its_interval_is_off():
    """The default. A megabyte on a timer is the part that needs consent."""
    clock, spy = Clock(), Spy()
    cfg = Cfg(speed_interval=0.0)
    svc = _service(clock, spy, cfg)
    await svc.refresh()
    clock.advance(3600.0)
    await svc.refresh()

    assert spy.speed_calls == 0
    assert svc.state.speed is None


@pytest.mark.asyncio
async def test_the_speed_probe_runs_when_an_interval_is_configured():
    clock, spy = Clock(), Spy()
    cfg = Cfg(speed_interval=300.0)
    svc = _service(clock, spy, cfg)
    await svc.refresh()

    assert spy.speed_calls == 1
    assert svc.state.speed.complete


@pytest.mark.asyncio
async def test_force_speed_overrides_the_interval_being_off():
    """What `/net` does: you asked, so the reading is taken."""
    clock, spy = Clock(), Spy()
    svc = _service(clock, spy, Cfg(speed_interval=0.0))
    await svc.refresh(force_speed=True)

    assert spy.speed_calls == 1


@pytest.mark.asyncio
async def test_a_disabled_service_never_starts_its_task():
    clock, spy = Clock(), Spy()
    svc = _service(clock, spy, Cfg(enabled=False))
    svc.start()
    assert not svc.started
    await svc.stop()


@pytest.mark.asyncio
async def test_start_is_idempotent_so_the_ui_tick_can_call_it_every_second():
    clock, spy = Clock(), Spy()
    svc = _service(clock, spy)
    svc.start()
    first = svc._task
    svc.start()
    assert svc._task is first
    await svc.stop()
    assert not svc.started


@pytest.mark.asyncio
async def test_a_probe_that_raises_does_not_escape_refresh():
    """The UI tick must never see an exception from here. The probes are
    written not to raise; this pins the service's own guarantee in case one
    day one of them does."""
    clock = Clock()

    async def boom(*a, **kw):
        raise RuntimeError("kaboom")

    svc = NetService(
        Cfg(), probes=Probes(reach=boom, trace=boom, throughput=boom), clock=clock
    )
    await svc.refresh()
    assert not svc.state.reach.ok
    assert svc.state.reach.error
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_net_service.py -v`

Expected: collection ERROR — `ModuleNotFoundError: No module named 'aegis.net.service'`.

- [ ] **Step 3: Write the service**

Create `src/aegis/net/service.py`:

```python
"""Polls the three probes on three cadences and caches the answer.

`state` is a plain attribute read, so the 1-second UI tick never touches the
network — the same division `QuotaService` draws (`aegis/usage/quota.py`).
This service holds one asyncio task and no thread: the probes are already
async, where the quota endpoint is blocking stdlib code that needed one.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, replace
from typing import Any, NamedTuple

from aegis.net.probe import Reach, Throughput, Trace


class Probes(NamedTuple):
    """The three callables, in one object so a test injects one thing."""

    reach: Any
    trace: Any
    throughput: Any


def default_probes() -> Probes:
    from aegis.net import probe

    return Probes(
        reach=lambda anchors, timeout: probe.reach(anchors, timeout),
        trace=lambda timeout: probe.trace(timeout=timeout),
        throughput=lambda nbytes, timeout: probe.throughput(
            nbytes=nbytes, timeout=timeout
        ),
    )


@dataclass(frozen=True)
class NetState:
    """The last reading of each fact, each with its own timestamp.

    Separate stamps because the renderer ages them independently: a
    throughput figure from four minutes ago is worth showing with its age,
    while an RTT from four minutes ago is not an RTT.
    """

    reach: Reach | None = None
    reach_at: float = 0.0
    trace: Trace | None = None
    trace_at: float = 0.0
    speed: Throughput | None = None
    speed_at: float = 0.0

    @property
    def sampled(self) -> bool:
        """Whether anything has been measured yet.

        The renderer shows an ellipsis rather than `0ms` until this is true:
        a zero claims a measurement.
        """
        return self.reach is not None


class NetService:
    def __init__(self, cfg, *, probes: Probes | None = None, clock=time.monotonic):
        self._cfg = cfg
        self._probes = probes or default_probes()
        self._clock = clock
        self._state = NetState()
        self._task: asyncio.Task | None = None
        # None until the first reading, so the very first success is not
        # mistaken for egress "coming back" and does not force a second trace
        # on top of the one the first refresh already takes.
        self._was_ok: bool | None = None

    @property
    def state(self) -> NetState:
        return self._state

    @property
    def started(self) -> bool:
        return self._task is not None

    def start(self) -> None:
        """Begin polling. Idempotent — safe to call from every UI tick."""
        if self._task is not None or not self._cfg.enabled:
            return
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    async def _loop(self) -> None:
        while True:
            await self.refresh()
            await asyncio.sleep(self._cfg.interval)

    async def refresh(self, *, force_speed: bool = False) -> None:
        """Take the readings that are due. Never raises."""
        now = self._clock()
        try:
            found = await self._probes.reach(self._cfg.anchors, self._cfg.timeout)
        except Exception as exc:  # noqa: BLE001 — a raise here must not reach the tick
            found = Reach(ok=False, error=type(exc).__name__)
        was, self._was_ok = self._was_ok, found.ok
        state = replace(self._state, reach=found, reach_at=now)

        if found.ok:
            # Its own cadence, plus the moment egress comes back: that
            # transition is when the address has actually had a chance to
            # change, because it is when you joined another network.
            came_back = was is False
            due = state.trace is None or now - state.trace_at >= self._cfg.trace_interval
            if due or came_back:
                state = self._with_trace(state, await self._trace(), now)

            want_speed = force_speed or (
                self._cfg.speed_interval > 0
                and (
                    state.speed is None
                    or now - state.speed_at >= self._cfg.speed_interval
                )
            )
            if want_speed:
                state = replace(state, speed=await self._speed(), speed_at=now)

        self._state = state

    @staticmethod
    def _with_trace(state: NetState, found: Trace, now: float) -> NetState:
        """Keep the last known address when a lookup fails.

        Losing the lookup says nothing about what the IP was, and the row it
        would blank is the one that tells you which network you fell onto. A
        failure is recorded only when there is nothing to lose.
        """
        if found.ok or state.trace is None:
            return replace(state, trace=found, trace_at=now)
        return state

    async def _trace(self) -> Trace:
        try:
            return await self._probes.trace(self._cfg.timeout)
        except Exception as exc:  # noqa: BLE001
            return Trace(ok=False, error=type(exc).__name__)

    async def _speed(self) -> Throughput:
        try:
            return await self._probes.throughput(
                self._cfg.speed_bytes, self._cfg.timeout
            )
        except Exception as exc:  # noqa: BLE001
            return Throughput(ok=False, asked=self._cfg.speed_bytes,
                              error=type(exc).__name__)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_net_service.py -v`

Expected: all PASS.

- [ ] **Step 5: Prove the transition test can fail**

The forced re-trace is the one behaviour here a live network would rarely exercise, so its test is the one worth breaking.

```bash
cp src/aegis/net/service.py /tmp/service.orig
sed -i 's/came_back = was is False/came_back = False/' src/aegis/net/service.py
cmp /tmp/service.orig src/aegis/net/service.py && echo "MUTATION DID NOT APPLY — stop" || echo "mutation applied"
uv run pytest tests/test_net_service.py -v -k coming_back
```

Expected: `mutation applied`, then `test_egress_coming_back_forces_a_re_read_of_the_exit_ip` FAILS with "the transition did not force a re-read". Restore:

```bash
cp /tmp/service.orig src/aegis/net/service.py
uv run pytest tests/test_net_service.py -q
```

Expected: all PASS again.

- [ ] **Step 6: Commit**

```bash
git add src/aegis/net/service.py tests/test_net_service.py
git commit -F - <<'EOF'
feat(net): NetService — three cadences over one task

Shaped like QuotaService: start/stop/state/refresh, state is a plain
attribute read so the 1s UI tick never touches the network. One asyncio task
and no thread, because these probes are already async where the quota
endpoint is blocking stdlib code that needed one.

Two rules the tests pin, both of which a live network makes hard to
reproduce. Egress coming back forces a re-read of the exit IP, because that
transition is the one moment the address has had a chance to change —
polling for it is waste. And a failed reach or a failed lookup leaves the
last known address in place, because losing the lookup says nothing about
what the IP was, and the row it would blank is the one that tells you which
network you fell onto.

Refs #13

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 4: `NetworkConfig`, and the docs `rift` requires

**Files:**
- Modify: `src/aegis/config/__init__.py` (add `NetworkConfig` after `VoiceConfig` at `:22-27`)
- Modify: `src/aegis/config/yaml_loader.py` (import at `:30`, `AegisConfig` field near `:79`, the build call near `:302`, the constructor arg near `:322`, and `_build_network` beside `_build_voice` at `:353`)
- Modify: `docs/configuration.md` (a new `## Network` section)
- Test: `tests/test_net_config.py` (create)

**Interfaces:**
- Consumes: `parse_anchor` from Task 2.
- Produces: `NetworkConfig(enabled, interval, trace_interval, speed_interval, speed_bytes, timeout, anchors)` where `anchors` is `tuple[tuple[str, int], ...]` — **already parsed**, so `NetService` can pass it straight to `probe.reach`. `AegisConfig.network: NetworkConfig`.

**Why the anchors are parsed at load.** A malformed anchor discovered at probe time is a row that reads "not sampled yet" forever with no explanation. `ConfigError` at load names the file, the key and the value.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_net_config.py`:

```python
"""The `network:` config block.

Anchors are parsed here rather than at probe time: a malformed one
discovered by the prober is a row that reads "not sampled yet" forever and
never says why.
"""
from __future__ import annotations

import pytest

from aegis.config import NetworkConfig
from aegis.config.yaml_loader import ConfigError, _build_network


def test_an_absent_block_gives_the_defaults():
    assert _build_network(None) == NetworkConfig()


def test_probing_is_on_by_default_and_the_megabyte_is_not():
    """300 bytes every five minutes is invisible. A repeating download from a
    speed-test host is the shape that gets noticed on a restricted network,
    so it ships off."""
    default = NetworkConfig()
    assert default.enabled is True
    assert default.speed_interval == 0.0


def test_the_default_anchors_are_already_parsed_pairs():
    """NetService hands these straight to probe.reach."""
    for entry in NetworkConfig().anchors:
        host, port = entry
        assert isinstance(host, str)
        assert isinstance(port, int)


def test_every_key_can_be_overridden():
    got = _build_network(
        {
            "enabled": False,
            "interval": 5,
            "trace_interval": 60,
            "speed_interval": 900,
            "speed_bytes": 2_000_000,
            "timeout": 1.5,
            "anchors": ["9.9.9.9:853"],
        }
    )
    assert got.enabled is False
    assert got.interval == 5.0
    assert got.trace_interval == 60.0
    assert got.speed_interval == 900.0
    assert got.speed_bytes == 2_000_000
    assert got.timeout == 1.5
    assert got.anchors == (("9.9.9.9", 853),)


def test_an_ipv6_anchor_survives_the_loader():
    """Review Focus 1, at the layer a user actually types it."""
    got = _build_network({"anchors": ["[2606:4700:4700::1111]:443"]})
    assert got.anchors == (("2606:4700:4700::1111", 443),)


def test_a_non_mapping_block_is_refused():
    with pytest.raises(ConfigError):
        _build_network(["1.1.1.1:443"])


def test_an_empty_anchor_list_is_refused_rather_than_silently_disabling():
    """Review Focus 5. With no anchors every probe fails and the row reads
    "not sampled yet" forever, which looks like a bug in aegis."""
    with pytest.raises(ConfigError):
        _build_network({"anchors": []})


def test_a_malformed_anchor_names_itself():
    with pytest.raises(ConfigError) as caught:
        _build_network({"anchors": ["1.1.1.1"]})
    assert "anchors" in str(caught.value)


def test_a_non_positive_speed_byte_count_is_refused():
    """Review Focus 5's other half."""
    with pytest.raises(ConfigError):
        _build_network({"speed_bytes": 0})


def test_the_service_reads_every_attribute_the_config_supplies():
    """The two halves of this feature agree on names.

    Task 3's tests run against a stand-in, so nothing there would notice a
    rename on this side. Bound from the real dataclass rather than a written
    list, so adding a field cannot leave this test passing by omission.
    """
    import inspect

    from aegis.net.service import NetService

    source = inspect.getsource(NetService)
    for name in NetworkConfig().__dataclass_fields__:
        assert f"_cfg.{name}" in source, (
            f"NetworkConfig.{name} is not read by NetService"
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_net_config.py -v`

Expected: collection ERROR — `ImportError: cannot import name 'NetworkConfig'`.

- [ ] **Step 3: Add `NetworkConfig`**

In `src/aegis/config/__init__.py`, immediately after the `VoiceConfig` dataclass (which ends at `:27`), add:

```python
@dataclass(frozen=True)
class NetworkConfig:
    """What the F3 NETWORK rows probe, and how often.

    Three cadences because the readings differ by a factor of three thousand
    in cost. `enabled` ships true: a handshake and a 300-byte lookup are
    invisible. `speed_interval` ships 0 — a repeating megabyte download from
    a speed-test host is the part that needs consent, and `/net` takes that
    reading on demand anyway.

    `anchors` is parsed at load rather than at probe time. A malformed entry
    found by the prober is a row that reads "not sampled yet" forever without
    saying why.
    """

    enabled: bool = True
    interval: float = 20.0
    trace_interval: float = 300.0
    speed_interval: float = 0.0  # 0 = off
    speed_bytes: int = 1_000_000
    timeout: float = 3.0
    anchors: tuple[tuple[str, int], ...] = (("1.1.1.1", 443), ("8.8.8.8", 443))
```

- [ ] **Step 4: Thread it through the loader**

In `src/aegis/config/yaml_loader.py`:

1. Add `NetworkConfig,` to the import block that already brings in `VoiceConfig` (around `:30`), keeping alphabetical order within that block.
2. In `AegisConfig`, immediately after the `voice: VoiceConfig = field(default_factory=VoiceConfig)` line (`:79`), add:

```python
    network: NetworkConfig = field(default_factory=NetworkConfig)
```

3. Beside `voice = _build_voice(raw.get("voice"))` (`:302`), add:

```python
    network = _build_network(raw.get("network"))
```

4. In the `AegisConfig(...)` construction, beside `voice=voice,` (`:322`), add:

```python
        network=network,
```

5. Immediately after `_build_voice` (which ends at `:367`), add:

```python
def _build_network(block: Any) -> NetworkConfig:
    """Build a NetworkConfig from a `network:` YAML block. Absent -> defaults."""
    from aegis.net.probe import parse_anchor

    defaults = NetworkConfig()
    if block is None:
        return defaults
    if not isinstance(block, dict):
        raise ConfigError("network: must be a mapping")

    raw_anchors = block.get("anchors")
    if raw_anchors is None:
        anchors = defaults.anchors
    else:
        if not isinstance(raw_anchors, list) or not raw_anchors:
            raise ConfigError(
                "network.anchors: must be a non-empty list of host:port — "
                "an empty list leaves every probe failing with nothing to say"
            )
        try:
            anchors = tuple(parse_anchor(str(a)) for a in raw_anchors)
        except ValueError as exc:
            raise ConfigError(f"network.anchors: {exc}") from exc

    speed_bytes = int(block.get("speed_bytes", defaults.speed_bytes))
    if speed_bytes <= 0:
        raise ConfigError("network.speed_bytes: must be a positive byte count")

    return NetworkConfig(
        enabled=bool(block.get("enabled", defaults.enabled)),
        interval=float(block.get("interval", defaults.interval)),
        trace_interval=float(block.get("trace_interval", defaults.trace_interval)),
        speed_interval=float(block.get("speed_interval", defaults.speed_interval)),
        speed_bytes=speed_bytes,
        timeout=float(block.get("timeout", defaults.timeout)),
        anchors=anchors,
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_net_config.py tests/test_yaml_loader.py -v`

Expected: all PASS. `test_yaml_loader.py` is included because it asserts on `AegisConfig`'s shape and a new field can break it.

- [ ] **Step 6: Document the section**

Add to `docs/configuration.md`, as a new `## Network` section placed after `## Voice input (push-to-talk)` (which ends at `:300`) and before `## Headless / Telegram`:

````markdown
## Network

What the F3 sidebar's `NET` rows probe, and how often. Three readings on
three cadences, because they differ by a factor of three thousand in cost.

```yaml
network:
  enabled: true            # false = no probes and no rows
  interval: 20             # seconds between egress/RTT checks
  trace_interval: 300      # seconds between exit-IP lookups
  speed_interval: 0        # 0 = off; seconds between throughput probes
  speed_bytes: 1000000     # bytes per throughput probe
  timeout: 3               # seconds before a probe gives up
  anchors:                 # host:port, tried concurrently
    - "1.1.1.1:443"
    - "8.8.8.8:443"
```

| key | default | what it does |
|---|---|---|
| `enabled` | `true` | Whether to probe at all. `false` removes the rows. |
| `interval` | `20` | Egress liveness and handshake RTT. One TCP handshake, no payload. |
| `trace_interval` | `300` | Exit IP, Cloudflare colo and country. About 300 bytes. Also re-read the moment egress comes back, because that is when the address has had a chance to change. |
| `speed_interval` | `0` | Seconds between throughput probes. `0` turns the timer off; `/net` still takes a reading on demand. |
| `speed_bytes` | `1000000` | Size of each throughput probe. Must be positive. |
| `timeout` | `3` | Per-probe timeout in seconds. |
| `anchors` | Cloudflare and Google resolvers on 443 | TCP targets for the liveness check. Two different operators, so one having a bad day does not read as "no egress". IPv6 literals are fine, bracketed or not. |

**Why TCP and not `ping`.** ICMP needs `CAP_NET_RAW` or a subprocess for
every sample, and a captive portal — the failure these rows exist to catch —
answers ICMP while refusing the connection. A completed handshake on 443
tests what actually breaks.

**Why the throughput timer ships off.** A liveness handshake and a 300-byte
lookup are invisible on any network. A repeating megabyte download from a
speed-test host is not, and on a restricted network it is the shape that gets
noticed. Turn it on where you own the link:

```yaml
network:
  speed_interval: 300
```

An empty `anchors` list and a `speed_bytes` of `0` are both refused at load
rather than accepted. Either one leaves every probe failing, and a row that
reads `· ⋯` forever looks like a defect in aegis rather than a setting.
````

- [ ] **Step 7: Run `rift` and the fast lane**

```bash
rift check
make test
```

Expected: `rift check` green — its "every config section is documented" rule is what Step 6 satisfies. `make test` rc=0.

- [ ] **Step 8: Commit**

```bash
git add src/aegis/config/__init__.py src/aegis/config/yaml_loader.py \
        docs/configuration.md tests/test_net_config.py
git commit -F - <<'EOF'
feat(config): a network: block, with its anchors parsed at load

Anchors become (host, port) pairs in the loader rather than at probe time,
so NetService hands them straight to probe.reach. A malformed entry found by
the prober is a row that reads "not sampled yet" forever and never says why;
ConfigError names the key and the value.

An empty anchor list and a speed_bytes of 0 are refused for the same reason:
both leave every probe failing, and that reads as a defect in aegis rather
than as a setting.

Refs #13

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 5: The rows on screen

This is the slice where a user first sees the feature. It wires the service into the app, pushes its state down to the pane, and renders it.

**Files:**
- Create: `src/aegis/tui/netmeter.py`
- Modify: `src/aegis/tui/sidebar.py` (`SidebarModel`'s `# SYSTEM` block around `:100-105`, and `_system()` at `:450-517`)
- Modify: `src/aegis/tui/pane.py` (a `set_net` beside `set_system` at `:1505`, and `_sidebar_model` at `:3134`)
- Modify: `src/aegis/tui/app.py` (build the service where the quota services are built; push it in `_tick` at `:1650-1678`)
- Test: `tests/test_netmeter.py` (create), `tests/test_sidebar_net.py` (create)
- Create: `changelog.d/13-f3-network-block.added.md`

**Interfaces:**
- Consumes: `NetState` (Task 3), `NetworkConfig` (Task 4), `format_age` from `aegis.render_shared:389`, `AegisColors` roles `ready`, `working`, `error`, `muted`, `accent` (`src/aegis/themes/__init__.py:14-25`).
- Produces:
  - `netmeter.format_net_tiers(state: NetState | None, colors, now: float) -> tuple[str, ...]`
  - `netmeter.format_exit_ip(state: NetState | None, colors) -> tuple[str, ...]`
  - `netmeter.RTT_WARN_MS: float`
  - `SidebarModel.net: tuple[str, ...]`, `SidebarModel.exit_ip: tuple[str, ...]`
  - `ConversationPane.set_net(state: NetState) -> None`
  - `AegisApp._net_service: NetService | None`

**Why the model carries strings.** `system`, `clock`, `cwd` and `build` are already pre-rendered tier tuples on `SidebarModel`; only the gauge sections carry numbers, because `gauge()` needs them. The net rows need a *clock* to age the throughput reading, and `pane._sidebar_model()` is where a live clock is already read (see its own comment at `:3161-3165`). Formatting there keeps `netmeter`'s functions pure and takes `time.monotonic()` out of the renderer.

- [ ] **Step 1: Write the failing renderer tests**

Create `tests/test_netmeter.py`:

```python
"""The NET row's strings. Pure functions, an explicit clock, no network."""
from __future__ import annotations

from aegis.net.probe import Reach, Throughput, Trace
from aegis.net.service import NetState
from aegis.themes import AegisColors
from aegis.tui.fit import strip_markup
from aegis.tui.netmeter import RTT_WARN_MS, format_exit_ip, format_net_tiers

PAL = AegisColors(
    ready="green", working="yellow", error="red", accent="cyan", muted="grey50",
    ok="green", err="red", user="white", user_bg="black",
)
NOW = 1000.0


def _plain(tiers) -> str:
    return " | ".join(strip_markup(t) for t in tiers)


def test_nothing_sampled_yet_shows_an_ellipsis_not_a_zero():
    """A zero claims a measurement. Same rule `_context` follows when it
    falls back to a tier rather than drawing a 0% bar."""
    got = _plain(format_net_tiers(NetState(), PAL, NOW))
    assert "⋯" in got
    assert "0ms" not in got


def test_a_live_link_shows_the_round_trip_time():
    state = NetState(reach=Reach(ok=True, rtt_ms=18.4), reach_at=NOW)
    got = _plain(format_net_tiers(state, PAL, NOW))
    assert "✓ 18ms" in got


def test_a_dead_link_says_no_egress_unmistakably():
    state = NetState(reach=Reach(ok=False, error="unreachable"), reach_at=NOW)
    tiers = format_net_tiers(state, PAL, NOW)
    assert "no egress" in _plain(tiers)
    assert PAL.error in tiers[0], "the failure is not coloured as one"


def test_a_healthy_round_trip_is_not_coloured_as_a_warning():
    state = NetState(reach=Reach(ok=True, rtt_ms=RTT_WARN_MS - 1), reach_at=NOW)
    assert PAL.ready in format_net_tiers(state, PAL, NOW)[0]


def test_a_slow_round_trip_is_coloured_as_a_warning():
    """Threshold bound from the module, not restated as a literal."""
    state = NetState(reach=Reach(ok=True, rtt_ms=RTT_WARN_MS + 1), reach_at=NOW)
    assert PAL.working in format_net_tiers(state, PAL, NOW)[0]


def test_the_widest_tier_carries_the_colo_and_the_speed():
    state = NetState(
        reach=Reach(ok=True, rtt_ms=18.0), reach_at=NOW,
        trace=Trace(ok=True, ip="1.2.3.4", colo="MIA"), trace_at=NOW,
        speed=Throughput(ok=True, bytes_per_s=139_712, received=1_000_000,
                         asked=1_000_000, elapsed_s=7.16),
        speed_at=NOW - 240.0,
    )
    widest = strip_markup(format_net_tiers(state, PAL, NOW)[0])
    assert "MIA" in widest
    # 139712 B/s * 8 / 1e6 = 1.12 Mbps. Derived, not written.
    assert f"{139_712 * 8 / 1e6:.1f} Mbps" in widest


def test_the_round_trip_time_is_in_every_tier():
    """It is the one reading with no other surface, so it must survive the
    narrowest column."""
    state = NetState(
        reach=Reach(ok=True, rtt_ms=18.0), reach_at=NOW,
        trace=Trace(ok=True, ip="1.2.3.4", colo="MIA"), trace_at=NOW,
        speed=Throughput(ok=True, bytes_per_s=1e5, received=10, asked=10,
                         elapsed_s=1.0),
        speed_at=NOW,
    )
    for tier in format_net_tiers(state, PAL, NOW):
        assert "18ms" in strip_markup(tier)


def test_the_colo_is_the_first_thing_to_go():
    state = NetState(
        reach=Reach(ok=True, rtt_ms=18.0), reach_at=NOW,
        trace=Trace(ok=True, ip="1.2.3.4", colo="MIA"), trace_at=NOW,
        speed=Throughput(ok=True, bytes_per_s=1e5, received=10, asked=10,
                         elapsed_s=1.0),
        speed_at=NOW,
    )
    tiers = [strip_markup(t) for t in format_net_tiers(state, PAL, NOW)]
    assert "MIA" in tiers[0]
    assert "MIA" not in tiers[1]
    assert "Mbps" in tiers[1]


def test_a_throughput_reading_always_carries_its_age():
    """A rate stops being true the moment the network changes, and an undated
    one invites a decision from a reading taken in another building."""
    state = NetState(
        reach=Reach(ok=True, rtt_ms=18.0), reach_at=NOW,
        speed=Throughput(ok=True, bytes_per_s=1e5, received=10, asked=10,
                         elapsed_s=1.0),
        speed_at=NOW - 240.0,
    )
    assert "4m ago" in strip_markup(format_net_tiers(state, PAL, NOW)[0])


def test_a_truncated_transfer_is_not_shown_as_a_rate():
    """Review Focus 4, at the surface. 200 KB of a megabyte computes a
    plausible figure, and putting it on screen is how it gets believed."""
    state = NetState(
        reach=Reach(ok=True, rtt_ms=18.0), reach_at=NOW,
        speed=Throughput(ok=True, bytes_per_s=9e6, received=200_000,
                         asked=1_000_000, elapsed_s=0.02),
        speed_at=NOW,
    )
    assert "Mbps" not in strip_markup(format_net_tiers(state, PAL, NOW)[0])


def test_the_exit_ip_is_one_row_with_one_tier():
    """One tier deliberately: `fit_rows` drops a segment whose narrowest
    still overflows, and a truncated address reads as a different address."""
    state = NetState(trace=Trace(ok=True, ip="2a0d:5600:6:202::15"), trace_at=NOW)
    tiers = format_exit_ip(state, PAL)
    assert len(tiers) == 1
    assert "2a0d:5600:6:202::15" in strip_markup(tiers[0])


def test_no_exit_ip_means_no_row():
    assert format_exit_ip(NetState(), PAL) == ()


def test_no_service_at_all_paints_nothing():
    """`network.enabled: false` builds no service, so nothing is ever pushed
    and SYSTEM must be exactly what it was before this feature existed. This
    is the case that must NOT show the `⋯` placeholder."""
    assert format_net_tiers(None, PAL, NOW) == ()
    assert format_exit_ip(None, PAL) == ()


def test_a_captive_portal_reads_as_reachable_with_no_address():
    """Review Focus 2, at the surface. The handshake completes, so the row
    honestly says the port answered — and the absence of an IP and a colo is
    the signal that nothing beyond it worked."""
    state = NetState(
        reach=Reach(ok=True, rtt_ms=4.0), reach_at=NOW,
        trace=Trace(ok=False, error="no exit ip"), trace_at=NOW,
    )
    widest = strip_markup(format_net_tiers(state, PAL, NOW)[0])
    assert "✓ 4ms" in widest
    assert "MIA" not in widest
    assert format_exit_ip(state, PAL) == ()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_netmeter.py -v`

Expected: collection ERROR — `ModuleNotFoundError: No module named 'aegis.tui.netmeter'`.

- [ ] **Step 3: Write `netmeter.py`**

Create `src/aegis/tui/netmeter.py`:

```python
"""The NET rows' strings — pure, with the clock passed in.

Beside `sysmeter.py` for the same reason it exists: a formatter that the
sidebar and the status bar both reach is not the sidebar's business, and a
function taking `(state, colors, now)` is testable without an app.
"""

from __future__ import annotations

from aegis.render_shared import format_age

# Past this, the path is worth a second look even though it works. Chosen to
# sit well above a healthy anycast resolver (tens of ms) and well below a
# timeout, so it flags a degraded link rather than a distant one.
RTT_WARN_MS = 200.0

_LABEL = "NET"
# Aligns the address under the label's own text rather than under the row.
_INDENT = " " * (len(_LABEL) + 2)


def _speed_segment(state, colors, now: float) -> str:
    """`↓ 1.1 Mbps 4m ago`, or nothing.

    Nothing when the transfer did not deliver what it asked for: a stream
    that dies part-way computes a perfectly plausible rate, and the screen is
    where a plausible wrong number gets believed.

    Always with its age. A rate stops being true the moment the network
    changes, and an undated one invites a decision from a reading taken
    somewhere else.
    """
    speed = getattr(state, "speed", None)
    if speed is None or not speed.complete:
        return ""
    mbps = speed.bytes_per_s * 8 / 1e6
    age = format_age(max(0.0, now - state.speed_at))
    return f"↓ {mbps:.1f} Mbps [{colors.muted}]{age}[/]"


def format_net_tiers(state, colors, now: float) -> tuple[str, ...]:
    """The `NET` row, widest tier first.

    `fit_rows` takes the widest that fits, so the ordering here decides what
    a narrow column keeps: the RTT is in every tier because it is the one
    reading with no other surface, the colo goes first because it is
    interesting rather than actionable, and the rate goes second because
    `/net` reprints it on demand.
    """
    head = f"[{colors.muted}]{_LABEL}[/]  "
    # Two different absences. No state at all means `network.enabled: false`:
    # no service was ever built, so SYSTEM must look exactly as it did before
    # this feature. A state that has not sampled yet is a service that exists
    # and has not answered, which is worth a row — and the row says `⋯`
    # rather than `0ms`, because a zero claims a measurement nobody made.
    if state is None:
        return ()
    if not state.sampled:
        return (f"{head}[{colors.muted}]· ⋯[/]",)
    found = state.reach
    if not found.ok:
        return (f"{head}[{colors.error}]✗ no egress[/]",)

    style = colors.working if found.rtt_ms >= RTT_WARN_MS else colors.ready
    live = f"[{style}]✓ {found.rtt_ms:.0f}ms[/]"
    trace = state.trace
    colo = trace.colo if trace is not None and trace.ok else ""
    speed = _speed_segment(state, colors, now)

    tiers = (
        " · ".join(part for part in (live, colo, speed) if part),
        " · ".join(part for part in (live, speed) if part),
        live,
    )
    # Deduplicated: with no colo and no rate all three are the same string,
    # and a tuple that repeats itself misreports how far the row can narrow.
    return tuple(f"{head}{tier}" for tier in dict.fromkeys(tiers))


def format_exit_ip(state, colors) -> tuple[str, ...]:
    """The exit-IP row. One tier, deliberately.

    `fit_rows` drops a segment whose narrowest tier still overflows rather
    than truncating it, so a column too narrow for the address loses the row
    whole — which is what we want, because `2a0d:5600:6:2…` reads as a
    different address rather than a clipped one. That is the failure `gauge`
    already hit when it truncated a RAM tail to `9.8/1`. A second, shorter
    tier would recreate it.
    """
    trace = getattr(state, "trace", None)
    if trace is None or not trace.ok or not trace.ip:
        return ()
    return (f"{_INDENT}[{colors.muted}]{trace.ip}[/]",)
```

- [ ] **Step 4: Run the renderer tests to verify they pass**

Run: `uv run pytest tests/test_netmeter.py -v`

Expected: all PASS.

- [ ] **Step 5: Write the failing wiring test**

Create `tests/test_sidebar_net.py`:

```python
"""The NETWORK rows, in a running app.

Assertions read what the widget painted, never the model field: a model
assertion is green against a section that was never composed into
`SECTIONS`, which is what this file exists to catch. Same seam as
`tests/test_sidebar_system.py`.
"""
from __future__ import annotations

import pytest

from aegis.config import Agent
from aegis.events import Result
from aegis.net.probe import Reach, Trace
from aegis.net.service import NetState
from aegis.tui.app import AegisApp
from aegis.tui.sidebar import Sidebar


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


class FakeSession:
    session_id = "sid-1"

    def __init__(self):
        self.sent = []

    async def start(self): pass
    async def send(self, text): self.sent.append(text)

    async def events(self):
        yield Result(duration_ms=1, is_error=False)

    async def close(self): pass


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"

    def bind(self, bridge): self.bound = bridge
    async def start(self): pass
    async def stop(self): pass


def _app():
    def make(agent, mcp_url, handle, **kw):
        return FakeSession()
    return AegisApp({"default": _agent()}, "default", make, FakeMCP())


def _painted(pane) -> str:
    sidebar = pane.query_one(Sidebar)
    assert sidebar._paints > 0, "the sidebar never painted"
    return sidebar.plain()


@pytest.mark.asyncio
async def test_the_system_block_carries_the_network_rows():
    app = _app()
    async with app.run_test(size=(120, 40)) as pilot:
        pane = app._panes[0]
        pane.toggle_task_dock()
        pane.set_net(
            NetState(
                reach=Reach(ok=True, rtt_ms=18.0), reach_at=0.0,
                trace=Trace(ok=True, ip="2a0d:5600:6:202::15", colo="MIA"),
                trace_at=0.0,
            )
        )
        await pilot.pause()

        painted = _painted(pane)
        assert "SYSTEM" in painted
        assert "NET" in painted
        assert "18ms" in painted
        assert "2a0d:5600:6:202::15" in painted


@pytest.mark.asyncio
async def test_a_dead_link_is_visible_in_the_painted_column():
    app = _app()
    async with app.run_test(size=(120, 40)) as pilot:
        pane = app._panes[0]
        pane.toggle_task_dock()
        pane.set_net(NetState(reach=Reach(ok=False, error="unreachable"),
                              reach_at=0.0))
        await pilot.pause()
        assert "no egress" in _painted(pane)


@pytest.mark.asyncio
async def test_the_network_rows_sit_above_the_clock_row():
    """SECTIONS orders by volatility and `_system` applies it one level down:
    the RTT moves every 20s, the clock every minute, cwd and build never."""
    app = _app()
    async with app.run_test(size=(120, 40)) as pilot:
        pane = app._panes[0]
        pane.toggle_task_dock()
        pane.set_net(NetState(reach=Reach(ok=True, rtt_ms=18.0), reach_at=0.0))
        await pilot.pause()

        rows = _painted(pane).split("\n")
        net_at = next(i for i, r in enumerate(rows) if "NET" in r)
        cwd_at = next(i for i, r in enumerate(rows) if "CWD" in r)
        assert net_at < cwd_at


@pytest.mark.asyncio
async def test_a_narrow_column_keeps_the_reading_and_drops_the_address():
    """`fit_rows` drops a segment whose narrowest tier overflows. The address
    is the half with another surface (`/net`); the reading is not."""
    app = _app()
    async with app.run_test(size=(120, 40)) as pilot:
        pane = app._panes[0]
        pane.toggle_task_dock()
        pane.set_net(
            NetState(
                reach=Reach(ok=True, rtt_ms=18.0), reach_at=0.0,
                trace=Trace(ok=True, ip="2a0d:5600:6:202::15", colo="MIA"),
                trace_at=0.0,
            )
        )
        await pilot.pause()
        sidebar = pane.query_one(Sidebar)

        from aegis.tui.sidebar import render_sidebar

        narrow = render_sidebar(sidebar._model, sidebar._palette, 24).plain
        assert "18ms" in narrow
        assert "2a0d:5600:6:202::15" not in narrow


@pytest.mark.asyncio
async def test_no_network_state_means_no_network_rows():
    """`network.enabled: false` never builds a service, so nothing is pushed
    and the block is exactly what it was before this feature."""
    app = _app()
    async with app.run_test(size=(120, 40)) as pilot:
        pane = app._panes[0]
        pane.toggle_task_dock()
        await pilot.pause()
        painted = _painted(pane)
        assert "SYSTEM" in painted
        assert "NET" not in painted
```

- [ ] **Step 6: Run it to verify it fails**

Run: `uv run pytest tests/test_sidebar_net.py -v`

Expected: FAIL — `AttributeError: 'ConversationPane' object has no attribute 'set_net'`.

- [ ] **Step 7: Add the model fields and render them**

In `src/aegis/tui/sidebar.py`, inside the `# SYSTEM` block of `SidebarModel` (after `stats: SystemStats | None = None`), add:

```python
    # Pre-rendered like `system` / `clock` / `cwd` / `build` rather than
    # carried as a `NetState`: the rate needs its age, ageing needs a clock,
    # and `pane._sidebar_model` is where a live clock is already read. Keeps
    # this renderer pure.
    net: tuple[str, ...] = ()
    exit_ip: tuple[str, ...] = ()
```

In `_system()`, immediately after the `if m.stats is not None: ... else: ...` block and **before** the `rows += _rows([Segment("clock", ...)])` line, add:

```python
    # Above the clock: the reading moves every 20 seconds, the clock every
    # minute. Two segments rather than one so a narrow column can keep the
    # reading and drop the address — see `format_exit_ip` on why the address
    # must never be truncated.
    rows += _rows(
        [Segment("net", m.net, 0), Segment("exit_ip", m.exit_ip, 0)],
        palette,
        width,
    )
```

- [ ] **Step 8: Add `set_net` to the pane and fill the model**

In `src/aegis/tui/pane.py`, immediately after `set_system` (which ends at `:1518`), add:

```python
    def set_net(self, state) -> None:
        """Push the cached network readings (sampled app-side) to the sidebar.

        A sibling of `set_system` rather than a parameter on it: that method's
        contract is the status bar's system segment, and the network rows are
        the sidebar's alone.
        """
        self._net_state = state
        self._refresh_sidebar()
```

In the same file, initialise the attribute in `ConversationPane.__init__` beside the existing `self._system_stats` initialisation:

```python
        self._net_state = None
```

In `_sidebar_model()`, after the `stats=self._system_stats,` line, add:

```python
            net=format_net_tiers(self._net_state, self._palette, time.monotonic()),
            exit_ip=format_exit_ip(self._net_state, self._palette),
```

and add to the imports at the top of `pane.py`:

```python
from aegis.tui.netmeter import format_exit_ip, format_net_tiers
```

`self._net_state` stays `None` until the app pushes one, and both formatters
return `()` for `None` — so a session where `network.enabled: false` paints no
rows at all rather than a permanent `NET · ⋯`.

- [ ] **Step 9: Build and push the service in the app**

`AegisApp` has no `self._cfg`. It takes config blocks as constructor kwargs — `voice: "VoiceConfig | None" = None` at `app.py:428`, stored as `self._voice_cfg = voice or VoiceConfig()` at `:506`. Follow that exactly.

**9a. The app.** In `src/aegis/tui/app.py`, add to `__init__`'s signature beside `voice` (`:428`):

```python
        network: "NetworkConfig | None" = None,
```

and beside `self._voice_cfg` (`:506`):

```python
        # Same shape as `_voice_cfg`: a config block arrives as a kwarg, and
        # `or NetworkConfig()` means every boot path that has not been taught
        # to pass one still gets the defaults — which are enabled with the
        # megabyte timer off, so the rows work everywhere from day one.
        self._net_cfg = network or NetworkConfig()
        from aegis.net.service import NetService

        # Public like `quota_services`, because `/net` reaches it off the
        # bridge. None when configured off: the pane is then never pushed a
        # state, so SYSTEM is byte-for-byte what it was before.
        self.net_service = (
            NetService(self._net_cfg) if self._net_cfg.enabled else None
        )
```

with `from aegis.config import NetworkConfig` added to the imports.

**9b. The tick.** In `_tick()` (`:1650`), immediately after the `with contextlib.suppress(Exception): self._quota_tick(active)` block at `:1677-1678`, add:

```python
        if self.net_service is not None:
            with contextlib.suppress(Exception):
                self.net_service.start()  # idempotent
                if active is not None and hasattr(active, "set_net"):
                    active.set_net(self.net_service.state)
```

**9c. Shutdown.** In `action_quit`, beside the `for _service in self.quota_services.values(): await _service.stop()` loop at `:2508-2509`, add:

```python
        if self.net_service is not None:
            await self.net_service.stop()
```

This is also the teardown the `Ctrl+Q` terminal-hang entry in `TASKS.md` is about — a service built in `__init__` and never closed in `action_quit` is exactly that defect. Do not skip it.

**9d. The config chain.** Four edits in `src/aegis/cli.py`, so a user's `.aegis.yaml` actually reaches the app:

1. `BootConfig` (`:105-116`) — add `network: object | None` after `voice`.
2. `load_boot_config` (`:137-151`) — add `network=yaml_cfg.network,` after `voice=yaml_cfg.voice,`.
3. `ResolvedBoot.serve_kwargs` (`:290-305`) — add `"network": b.network,` after `"hosts": b.hosts,`.
4. `_serve`'s signature (`:523-541`) — add `network=None,` after `hosts: dict | None = None,`; and in the `ViewRegistry(...)` call at `:701-717`, add `network=network,` beside `cwd=str(roots.harness_cwd),`.

Also add `network=None,` to the `AegisApp(...)` call at `:231-242`, beside its existing `voice=None,` — that path deliberately boots without config.

**Note for the PR body, not a thing to fix here.** `voice` is in `BootConfig` but absent from `serve_kwargs`, so it never reaches the daemon boot path at all. That is pre-existing and out of scope; mention it in the PR so somebody can file it.

- [ ] **Step 10: Run the wiring tests and the fast lane**

```bash
uv run pytest tests/test_netmeter.py tests/test_sidebar_net.py tests/test_sidebar_system.py -v
make test
```

Expected: all PASS; `make test` rc=0.

- [ ] **Step 11: Prove the wiring test can fail**

The one that matters is the `_painted` assertion, because it is the one that catches a section that renders but was never composed.

```bash
cp src/aegis/tui/sidebar.py /tmp/sidebar.net.orig
python3 - <<'PY'
import re, pathlib
p = pathlib.Path("src/aegis/tui/sidebar.py")
s = p.read_text()
s = s.replace('[Segment("net", m.net, 0), Segment("exit_ip", m.exit_ip, 0)]',
              '[]')
p.write_text(s)
PY
cmp /tmp/sidebar.net.orig src/aegis/tui/sidebar.py && echo "MUTATION DID NOT APPLY — stop" || echo "mutation applied"
uv run pytest tests/test_sidebar_net.py -v
```

Expected: `mutation applied`, then the four rows-present tests FAIL while `test_no_network_state_means_no_network_rows` still passes. Restore:

```bash
cp /tmp/sidebar.net.orig src/aegis/tui/sidebar.py
uv run pytest tests/test_sidebar_net.py -q
```

- [ ] **Step 12: Exercise it the way a user reaches it**

`AGENTS.md` is explicit that green tests against a daemon that booted before the change prove nothing. Run the real TUI.

```bash
cat > /tmp/aegis-net-check.yaml <<'EOF'
network:
  enabled: true
  interval: 5
  speed_interval: 60
  speed_bytes: 1000000
EOF
```

Merge that `network:` block into the `.aegis.yaml` this worktree loads, then:

```bash
uv run aegis
```

Press `F3`. Confirm by eye, and write down what you saw for the PR body:

1. A `NET` row appears in `SYSTEM` within ~5 seconds, reading `✓ Nms`.
2. An exit-IP row appears under it. On zion this reads as an IPv6 address.
3. A colo appears (`MIA` on zion today).
4. Within a minute a `↓ N.N Mbps` figure appears with an age beside it.
5. Disconnect the network (turn off wifi, or `sudo ip link set <iface> down`). Within ~10 seconds the row reads `✗ no egress` **in red**, and the exit-IP row is still there.
6. Reconnect. The row goes green again.
7. Narrow the terminal until the sidebar is thin. The address row disappears; the reading stays.
8. `Ctrl+Q` exits with rc=0 and no traceback.

Then remove the `network:` block again and confirm `F3` shows a `SYSTEM` block with no `NET` row.

- [ ] **Step 13: Write the changelog fragment**

```bash
cat > changelog.d/13-f3-network-block.added.md <<'EOF'
- **F3's SYSTEM block now answers whether the network works.** A `NET` row
  carries egress liveness with its handshake RTT, the Cloudflare colo, and an
  opt-in throughput figure; the row under it carries the exit IP. The two
  questions it exists for are the ones that decide what an agent should do
  next — on a captive network egress dies silently and the failures arrive
  later looking like bugs in whatever was being built, and a link measured at
  139 KB/s is forty times under the threshold where work stays local instead
  of going to a server.

  Three readings on three cadences, because they differ by a factor of three
  thousand in cost: a TCP handshake every 20s, a 300-byte lookup every 300s
  and whenever egress comes back, and a megabyte download only when
  `network.speed_interval` is set or you type `/net`. TCP rather than ICMP,
  because a captive portal answers `ping` and refuses the connection. A
  throughput figure is never shown without its age, and never at all when the
  transfer delivered less than it asked for.
EOF
make changelog-check
```

- [ ] **Step 14: Commit**

```bash
git add src/aegis/tui/netmeter.py src/aegis/tui/sidebar.py src/aegis/tui/pane.py \
        src/aegis/tui/app.py tests/test_netmeter.py tests/test_sidebar_net.py \
        changelog.d/13-f3-network-block.added.md
git commit -F - <<'EOF'
feat(tui): NET rows in F3's SYSTEM block

Two segments above the clock: the reading, and the exit IP. Pre-rendered in
`pane._sidebar_model` like `system`/`clock`/`cwd`/`build` rather than carried
as a NetState, because ageing the throughput figure needs a clock and that
method is where a live one is already read — which keeps `netmeter`'s
functions pure and takes `time.monotonic` out of the renderer.

Two segments rather than one so a narrow column keeps the reading and drops
the address. The address gets a single tier on purpose: `fit_rows` drops a
segment whose narrowest still overflows, and `2a0d:5600:6:2…` reads as a
different address rather than a clipped one — the failure `gauge` already hit
printing a RAM tail as `9.8/1`.

A disabled service is never built, so nothing is pushed and SYSTEM is
byte-for-byte what it was before.

Closes #13

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

### Task 6: `/net`

**Files:**
- Create: `src/aegis/commands/builtins/net.py`
- Modify: `src/aegis/commands/builtins/__init__.py` (import the new module so it registers)
- Modify: `docs/commands.md` (a `/net` row — `rift` requires it)
- Test: `tests/test_net_command.py` (create)

**Interfaces:**
- Consumes: `NetService`, `NetState` (Task 3), `NetworkConfig` (Task 4), `CommandContext`, `CommandResult`, `SlashCommand`, `register` (`src/aegis/commands/__init__.py:28-45`).
- Produces: a registered `SlashCommand("net", …)`. Nothing depends on it.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_net_command.py`:

```python
"""`/net` — a forced reading, printed with the inputs it came from."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from aegis.commands import REGISTRY
from aegis.net.probe import Reach, Throughput, Trace
from aegis.net.service import NetState


class FakeService:
    def __init__(self, state):
        self._state = state
        self.forced = []

    @property
    def state(self):
        return self._state

    async def refresh(self, *, force_speed=False):
        self.forced.append(force_speed)


def _ctx(service):
    return SimpleNamespace(
        bridge=SimpleNamespace(net_service=service), handle="h-1"
    )


def _state():
    return NetState(
        reach=Reach(
            ok=True, rtt_ms=18.0, anchor="1.1.1.1:443",
            per_anchor=(("1.1.1.1:443", 18.0), ("8.8.8.8:443", None)),
        ),
        reach_at=0.0,
        trace=Trace(ok=True, ip="2a0d:5600:6:202::15", colo="MIA", loc="US"),
        trace_at=0.0,
        speed=Throughput(ok=True, bytes_per_s=139_712, received=1_000_000,
                         asked=1_000_000, elapsed_s=7.16),
        speed_at=0.0,
    )


def test_the_command_is_registered():
    assert "net" in REGISTRY


@pytest.mark.asyncio
async def test_it_forces_a_throughput_reading():
    """The timer ships off, so asking is the whole point of the command."""
    svc = FakeService(_state())
    await REGISTRY["net"].run(_ctx(svc), {})
    assert svc.forced == [True]


@pytest.mark.asyncio
async def test_it_prints_the_exit_ip_the_colo_and_the_location():
    got = await REGISTRY["net"].run(_ctx(FakeService(_state())), {})
    assert got.ok
    assert "2a0d:5600:6:202::15" in got.body
    assert "MIA" in got.body
    assert "US" in got.body


@pytest.mark.asyncio
async def test_it_prints_a_reading_for_every_anchor_not_just_the_winner():
    got = await REGISTRY["net"].run(_ctx(FakeService(_state())), {})
    assert "1.1.1.1:443" in got.body
    assert "8.8.8.8:443" in got.body


@pytest.mark.asyncio
async def test_it_prints_the_inputs_the_rate_was_computed_from():
    """A number with its inputs is arguable; without them it is oracular."""
    got = await REGISTRY["net"].run(_ctx(FakeService(_state())), {})
    assert f"{139_712 * 8 / 1e6:.1f} Mbps" in got.body
    assert "1000000" in got.body
    assert "7.16" in got.body


@pytest.mark.asyncio
async def test_a_truncated_transfer_is_reported_as_unmeasured():
    """Review Focus 4 at this surface too: the rate is plausible and wrong,
    so the command names the shortfall instead of printing the figure."""
    state = NetState(
        reach=Reach(ok=True, rtt_ms=18.0, per_anchor=(("1.1.1.1:443", 18.0),)),
        reach_at=0.0,
        speed=Throughput(ok=True, bytes_per_s=9e6, received=200_000,
                         asked=1_000_000, elapsed_s=0.02),
        speed_at=0.0,
    )
    got = await REGISTRY["net"].run(_ctx(FakeService(state)), {})
    assert "Mbps" not in got.body
    assert "200000" in got.body


@pytest.mark.asyncio
async def test_a_dead_link_says_so_in_the_title():
    state = NetState(
        reach=Reach(ok=False, error="unreachable",
                    per_anchor=(("1.1.1.1:443", None),)),
        reach_at=0.0,
    )
    got = await REGISTRY["net"].run(_ctx(FakeService(state)), {})
    assert "no egress" in got.title


@pytest.mark.asyncio
async def test_it_explains_itself_when_probing_is_switched_off():
    ctx = SimpleNamespace(bridge=SimpleNamespace(net_service=None), handle="h-1")
    got = await REGISTRY["net"].run(ctx, {})
    assert not got.ok
    assert "network" in got.title.lower() or "network" in got.body.lower()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_net_command.py -v`

Expected: FAIL — `KeyError: 'net'` on the registration test and every test that indexes `REGISTRY`.

- [ ] **Step 3: Write the command**

Create `src/aegis/commands/builtins/net.py`:

```python
"""``/net`` — a forced network reading, printed with its inputs.

    /net    exit IP, colo, RTT per anchor, and a throughput sample

Takes the throughput reading regardless of ``network.speed_interval``,
because the timer ships off and asking is the point. Read-only.
"""

from __future__ import annotations

from aegis.commands import CommandContext, CommandResult, SlashCommand, register

_SERVICE = None


def _service(ctx):
    """The app's NetService, else a private one on the defaults.

    Mirrors ``builtins/usage.py:_quota_services``: the command has to work
    headlessly under ``aegis serve``, where no TUI app owns a service.

    The fallback uses ``NetworkConfig()`` rather than reading the file. The
    app is the thing that holds the loaded config, so if there is no app there
    is no cheap way to ask, and the defaults are what this command needs
    anyway: it forces its own reading, so the intervals are irrelevant and
    only the anchors and the timeout matter.

    Returns None only when the app exists and has probing switched off —
    ``AegisApp.net_service`` is None in exactly that case, and the difference
    between "off" and "no app" is whether the attribute is present at all.
    """
    global _SERVICE
    bridge = getattr(ctx, "bridge", None)
    if bridge is not None and hasattr(bridge, "net_service"):
        return bridge.net_service  # may be None: probing is configured off
    if _SERVICE is None:
        from aegis.config import NetworkConfig
        from aegis.net.service import NetService

        _SERVICE = NetService(NetworkConfig())
    return _SERVICE


def _lines(state) -> list[str]:
    found = state.reach
    out = [f"egress    {'up' if found.ok else 'DOWN'}"]
    for label, rtt in found.per_anchor:
        out.append(f"  {label:<26}{'—' if rtt is None else f'{rtt:.0f}ms'}")
    trace = state.trace
    if trace is not None and trace.ok:
        out.append(f"exit ip   {trace.ip}")
        if trace.colo:
            where = f"{trace.colo} ({trace.loc})" if trace.loc else trace.colo
            out.append(f"colo      {where}")
    speed = state.speed
    if speed is None:
        return out
    # The inputs, always. A rate with the bytes and the seconds it came from
    # is a number someone can argue with; without them it is an oracle.
    if speed.complete:
        mbps = speed.bytes_per_s * 8 / 1e6
        out.append(
            f"down      {mbps:.1f} Mbps "
            f"({speed.received} bytes in {speed.elapsed_s:.2f}s)"
        )
    else:
        out.append(
            f"down      unmeasured — {speed.error or 'short transfer'} "
            f"({speed.received} of {speed.asked} bytes "
            f"in {speed.elapsed_s:.2f}s)"
        )
    return out


async def _net(ctx: CommandContext, args) -> CommandResult:
    service = _service(ctx)
    if service is None:
        return CommandResult(
            False,
            "network probing is off",
            "set `network.enabled: true` in .aegis.yaml — see docs/configuration.md",
        )
    await service.refresh(force_speed=True)
    state = service.state
    if not state.sampled:
        return CommandResult(False, "net · no reading", "the probe returned nothing")
    return CommandResult(
        True,
        "net · " + ("up" if state.reach.ok else "no egress"),
        "\n".join(_lines(state)),
    )


register(
    SlashCommand(
        "net",
        "exit IP, egress liveness and a throughput reading",
        "/net",
        _net,
    )
)
```

- [ ] **Step 4: Register the module**

`src/aegis/commands/builtins/__init__.py` imports every submodule for its registration side-effect. Add one line, matching the existing six exactly:

```python
from aegis.commands.builtins import net as _net  # noqa: F401
```

The `# noqa: F401` is required — `ruff` flags the unused import otherwise, and the import *is* the registration.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_net_command.py -v`

Expected: all PASS.

- [ ] **Step 6: Document the command**

Add a `/net` row to the commands table in `docs/commands.md`, matching the existing rows' format:

```markdown
| `/net` | Exit IP, egress liveness with RTT per anchor, and a throughput reading. Forces the throughput probe regardless of `network.speed_interval`, and prints the byte count and elapsed time the rate was computed from. Read-only. |
```

- [ ] **Step 7: Run `rift` and the full gate**

```bash
rift check
make test
```

Expected: `rift check` green — its "every slash command is documented" rule is what Step 6 satisfies, and it will fail loudly if the row is missing. `make test` rc=0.

- [ ] **Step 8: Exercise it in a real TUI**

```bash
uv run aegis
```

Type `/net`. Confirm the transcript block shows the exit IP, the colo, a line per anchor, and a `down` line carrying the bytes and seconds. Record the output for the PR body. Then `Ctrl+Q` and confirm rc=0.

- [ ] **Step 9: Commit**

```bash
git add src/aegis/commands/builtins/net.py src/aegis/commands/builtins/__init__.py \
        docs/commands.md tests/test_net_command.py
git commit -F - <<'EOF'
feat(commands): /net — a forced reading, printed with its inputs

Forces the throughput probe regardless of network.speed_interval, because
the timer ships off and asking is the point. Prints a line per anchor rather
than only the winner, and the rate with the byte count and elapsed time it
was divided from: a number with its inputs is one someone can argue with.

A short transfer is reported as unmeasured with the shortfall named, not as
the plausible rate it computes.

Refs #13

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
```

---

## Closing out

- [ ] **Run every gate, reading each exit code directly**

```bash
make test        ; echo "test rc=$?"
rift check       ; echo "rift rc=$?"
make changelog-check ; echo "changelog rc=$?"
uv run ruff format src/ && uv run ruff check --fix src/
```

Then reproduce the CI runner, which colours `--help` only when `GITHUB_ACTIONS` is set:

```bash
GITHUB_ACTIONS=true uv run pytest -q -m "not live"; echo "ci-shape rc=$?"
```

Expected: rc=0 everywhere. `make check` will still exit 2 on the advisory `ty` stage — that is issue #8 and pre-existing. Confirm the count has not grown: `make typecheck` and compare against 356.

- [ ] **Push and open the PR**

```bash
git push -u origin f3-network-and-tps
```

PR body carries what a reviewer cannot re-derive:

- The two issues it closes, and that both land in `sidebar.py`, which is why they share a branch.
- **The measurements**: the baseline suite (4702/45/rc=0), the live `curl` readings from zion (139 KB/s, IPv6 exit via MIA), and the eight-point manual walkthrough from Task 5 Step 12 with what was actually seen at each point.
- **What was tried and rejected**: ICMP (needs `CAP_NET_RAW` or a subprocess, and a captive portal answers it); `ifconfig.me` as the primary (one fact per request against `cdn-cgi/trace`'s three); a second shorter tier for the exit IP (would recreate `gauge`'s `9.8/1` truncation defect); `priority` on the two segments (`fit_rows` never consults it).
- **Deliberately out of scope**: RTT history, per-interface breakdown, dual-stack reporting, VPN detection.
- **One flag for Alex**: the exit IP renders on a surface `webterm` serves, and stage 6's remaining task drops Caddy's basic auth from `dev.apiad.net`. `network.enabled: false` in the VPS config is the escape hatch.

- [ ] **Confirm CI, and whether a red run is even yours**

```bash
gh pr checks --watch
```

If red, diff the failure lists against `main` before concluding it is yours:

```bash
gh run view <pr-run-id>   --log-failed | grep -oE "^FAILED tests/[^ ]+" | sort -u > /tmp/pr.txt
gh run view <main-run-id> --log-failed | grep -oE "^FAILED tests/[^ ]+" | sort -u > /tmp/main.txt
diff /tmp/main.txt /tmp/pr.txt
```

- [ ] **Merge on green, then confirm the merge rather than believing the error**

```bash
gh pr merge <n> --rebase --delete-branch
gh pr view <n> --json state,mergeCommit --jq '{state, oid: .mergeCommit.oid}'
```

`gh pr merge` from inside a worktree prints `fatal: 'main' is already used by worktree …` **after** the merge has already succeeded. Read the JSON, not the error.

- [ ] **Update the spec's status header and `TASKS.md`**

Flip the spec's `**Status:**` from "no code yet" to what shipped, with the commit range. Add an entry to `TASKS.md` only if something was left undone.

- [ ] **Clean up**

```bash
git fetch origin --prune
git worktree remove .claude/worktrees/f3-network-and-tps
```
