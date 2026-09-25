"""The reconciler: reap, gate, start."""
from __future__ import annotations

import json

import pytest

from aegis.workflows.builtins.afk import tick as tick_mod
from aegis.workflows.builtins.afk.board import Card, Schema, parse_marker, render_marker

SCHEMA = Schema(
    project_id="PVT_p",
    field_ids={"Status": "F_s", "Repo": "F_r", "Priority": "F_p",
               "Deadline": "F_d", "Progress": "F_g", "Waiting on": "F_w"},
    option_ids={
        "Status": {n: f"o_{n}" for n in
                   ("Todo", "Waiting", "Running", "Needs review",
                    "Blocked", "Failed", "Done")},
        "Repo": {"aegis": "o_aegis"},
    },
)


class FakeEngine:
    """Only the engine surface this package touches."""

    def __init__(self, *, tasks=None, bash_results=None) -> None:
        self._tasks = tasks or {}
        self._bash = bash_results or {}
        self.enqueued: list[tuple[str, str]] = []
        self.bashed: list[str] = []
        self.logged: list[str] = []

    def task_status(self, task_id):
        return self._tasks.get(task_id)

    async def enqueue(self, queue, payload, *, callback=False):
        self.enqueued.append((queue, payload))
        return f"task-{len(self.enqueued)}"

    async def bash(self, cmd, cwd=None, **kw):
        self.bashed.append(cmd)
        for needle, res in self._bash.items():
            if needle in cmd:
                return res
        if "gh auth status" in cmd:
            return {"exit": 0, "stdout": "Token scopes: 'repo', 'project'"}
        return {"exit": 0, "stdout": ""}

    def log(self, msg):
        self.logged.append(msg)


def _cfg(tmp_path, **over):
    cfg = {
        "owner": "o", "owner_type": "org", "project": 3,
        "repo_root": str(tmp_path), "worker_queue": "afk",
        "max_in_flight": 5, "weekly_stop_at": 60, "session_stop_at": 70,
        "max_attempts": 2, "review_changed_files": 5, "vague_body_chars": 400,
        "acceptance_markers": ("done when",),
        "gate_commands": ("make check", "make test"),
        "priority_order": ("Urgent", "Important", "Normal"),
    }
    cfg.update(over)
    return cfg


def _card(number, status, *, repo="aegis", body="- [ ] do it"):
    return Card(item_id=f"I{number}", number=number, repo="o/r",
                url=f"https://github.com/o/r/issues/{number}",
                title=f"card {number}", body=body, state="OPEN",
                fields={"Status": status, "Repo": repo})


def _marker(task, attempt=1, gate="make check"):
    """A marker as `_start_one` would have written it.

    The gate is written raw: `render_marker` shell-quotes its values, so a
    command with a space in it survives the round trip to `parse_marker`.
    """
    return render_marker(task=task, attempt=attempt, gate=gate)


@pytest.fixture
def board(monkeypatch, tmp_path):
    """Intercept every board read and write; record the writes."""
    (tmp_path / "aegis").mkdir()
    (tmp_path / "aegis" / "Makefile").write_text("check:\n\t@echo ok\n")
    state = {"cards": [], "comments": {}, "fields": [], "quota": None}

    async def fetch_board(run, **kw):
        return SCHEMA, state["cards"]

    async def set_field(run, schema, card, *, field, value):
        state["fields"].append((card.number, field, value))

    async def upsert_comment(run, card, *, body):
        state["comments"][card.number] = body
        return "edited"

    async def fetch_comment(engine, card):
        return state["comments"].get(card.number, "")

    monkeypatch.setattr(tick_mod, "fetch_board", fetch_board)
    monkeypatch.setattr(tick_mod, "set_field", set_field)
    monkeypatch.setattr(tick_mod, "upsert_comment", upsert_comment)
    monkeypatch.setattr(tick_mod, "fetch_comment", fetch_comment)

    async def read_quota(*_a, **_k):
        return state["quota"]

    monkeypatch.setattr(tick_mod, "read_quota", read_quota)
    return state


def _quota(weekly, session, failure=""):
    from aegis.usage.quota import QuotaSnapshot, QuotaState, QuotaWindow
    return QuotaState(
        snapshot=QuotaSnapshot(
            windows=(QuotaWindow("weekly_all", weekly, "normal", None, True),
                     QuotaWindow("session", session, "normal", None, True)),
            fetched_at=0.0),
        failure=failure)


@pytest.mark.asyncio
async def test_starts_an_eligible_card(board, tmp_path) -> None:
    board["cards"] = [_card(1, "Todo")]
    board["quota"] = _quota(10, 10)
    engine = FakeEngine()
    await tick_mod.run_tick(engine, _cfg(tmp_path), now="2026-09-25T02:00:00Z")
    assert len(engine.enqueued) == 1
    assert (1, "Status", "Running") in board["fields"]
    assert "task=task-1" in board["comments"][1]


@pytest.mark.asyncio
async def test_the_gate_command_survives_the_marker_round_trip(board, tmp_path) -> None:
    """The coordinator re-runs the gate it told the worker about, and it
    learns which one that was from the marker it wrote itself. `render_marker`
    joins on spaces and `parse_marker` splits on them, so an unencoded
    `gate=make check` reads back as `make` and the coordinator would measure
    the wrong command."""
    board["cards"] = [_card(1, "Todo")]
    board["quota"] = _quota(10, 10)
    engine = FakeEngine()
    await tick_mod.run_tick(engine, _cfg(tmp_path), now="2026-09-25T02:00:00Z")
    marker = parse_marker(board["comments"][1])
    assert marker["gate"] == "make check"


@pytest.mark.asyncio
async def test_quota_gate_starts_nothing(board, tmp_path) -> None:
    board["cards"] = [_card(1, "Todo")]
    board["quota"] = _quota(65, 10)
    engine = FakeEngine()
    await tick_mod.run_tick(engine, _cfg(tmp_path), now="2026-09-25T02:00:00Z")
    assert engine.enqueued == []
    assert not any(f[1] == "Status" for f in board["fields"])


@pytest.mark.asyncio
async def test_unreadable_quota_starts_nothing(board, tmp_path) -> None:
    board["cards"] = [_card(1, "Todo")]
    board["quota"] = None
    engine = FakeEngine()
    await tick_mod.run_tick(engine, _cfg(tmp_path), now="2026-09-25T02:00:00Z")
    assert engine.enqueued == []


@pytest.mark.asyncio
async def test_reaps_a_green_report_to_needs_review(board, tmp_path) -> None:
    board["cards"] = [_card(1, "Running")]
    board["comments"][1] = _marker("t-1")
    board["quota"] = _quota(10, 10)
    report = ("```aegis-report\nstatus: needs-review\nsummary: did it\n"
              "gate: make check -> 0\nchanged: 1\n```")
    engine = FakeEngine(tasks={"t-1": {"status": "completed", "result": report,
                                       "worker_handle": "w1"}},
                        bash_results={"make check": {"exit": 0, "stdout": ""}})
    await tick_mod.run_tick(engine, _cfg(tmp_path), now="2026-09-25T02:00:00Z")
    assert (1, "Status", "Needs review") in board["fields"]


@pytest.mark.asyncio
async def test_a_lying_gate_lands_in_failed(board, tmp_path) -> None:
    """THE test this design exists for. The worker claims exit 0; the
    coordinator runs the same command and gets 1."""
    board["cards"] = [_card(1, "Running")]
    board["comments"][1] = _marker("t-1")
    board["quota"] = _quota(10, 10)
    report = ("```aegis-report\nstatus: needs-review\nsummary: did it\n"
              "gate: make check -> 0\nchanged: 1\n```")
    engine = FakeEngine(
        tasks={"t-1": {"status": "completed", "result": report,
                       "worker_handle": "w1"}},
        bash_results={"make check": {"exit": 1, "stdout": "2 tests failed"}})
    await tick_mod.run_tick(engine, _cfg(tmp_path), now="2026-09-25T02:00:00Z")
    assert "make check" in engine.bashed
    assert (1, "Status", "Failed") in board["fields"]
    assert "2 tests failed" in board["comments"][1]


@pytest.mark.asyncio
async def test_an_honest_red_gate_also_lands_in_failed(board, tmp_path) -> None:
    """The companion to the test above: it must distinguish a lie from an
    honest failure by moving both to Failed for different stated reasons,
    not by treating any report as suspect."""
    board["cards"] = [_card(1, "Running")]
    board["comments"][1] = _marker("t-1")
    board["quota"] = _quota(10, 10)
    report = ("```aegis-report\nstatus: failed\nsummary: could not fix it\n"
              "gate: make check -> 1\n```")
    engine = FakeEngine(
        tasks={"t-1": {"status": "completed", "result": report,
                       "worker_handle": "w1"}},
        bash_results={"make check": {"exit": 1, "stdout": "2 tests failed"}})
    await tick_mod.run_tick(engine, _cfg(tmp_path), now="2026-09-25T02:00:00Z")
    assert (1, "Status", "Failed") in board["fields"]
    assert "disagree" not in board["comments"][1].lower()


@pytest.mark.asyncio
async def test_an_honest_green_report_reaches_needs_review(board, tmp_path) -> None:
    """Proves the lying-gate check distinguishes rather than merely being
    red. Same card, same command, honest exit code."""
    board["cards"] = [_card(1, "Running")]
    board["comments"][1] = _marker("t-1")
    board["quota"] = _quota(10, 10)
    report = ("```aegis-report\nstatus: needs-review\nsummary: did it\n"
              "gate: make check -> 0\nchanged: 1\n```")
    engine = FakeEngine(
        tasks={"t-1": {"status": "completed", "result": report,
                       "worker_handle": "w1"}},
        bash_results={"make check": {"exit": 0, "stdout": ""}})
    await tick_mod.run_tick(engine, _cfg(tmp_path), now="2026-09-25T02:00:00Z")
    assert (1, "Status", "Needs review") in board["fields"]


@pytest.mark.asyncio
async def test_an_unparseable_report_lands_in_failed(board, tmp_path) -> None:
    board["cards"] = [_card(1, "Running")]
    board["comments"][1] = _marker("t-1")
    board["quota"] = _quota(10, 10)
    engine = FakeEngine(tasks={"t-1": {"status": "completed",
                                       "result": "I finished, trust me.",
                                       "worker_handle": "w1"}})
    await tick_mod.run_tick(engine, _cfg(tmp_path), now="2026-09-25T02:00:00Z")
    assert (1, "Status", "Failed") in board["fields"]
    assert "trust me" in board["comments"][1]


@pytest.mark.asyncio
async def test_an_orphaned_running_card_returns_to_todo(board, tmp_path) -> None:
    board["cards"] = [_card(1, "Running")]
    board["comments"][1] = _marker("t-gone")
    board["quota"] = _quota(10, 10)
    engine = FakeEngine(tasks={})
    await tick_mod.run_tick(engine, _cfg(tmp_path), now="2026-09-25T02:00:00Z")
    assert (1, "Status", "Todo") in board["fields"]
    assert "attempt=2" in board["comments"][1]


@pytest.mark.asyncio
async def test_an_orphan_past_max_attempts_is_blocked(board, tmp_path) -> None:
    board["cards"] = [_card(1, "Running")]
    board["comments"][1] = _marker("t-gone", attempt=2)
    board["quota"] = _quota(10, 10)
    engine = FakeEngine(tasks={})
    await tick_mod.run_tick(engine, _cfg(tmp_path, max_attempts=2),
                            now="2026-09-25T02:00:00Z")
    assert (1, "Status", "Blocked") in board["fields"]


@pytest.mark.asyncio
async def test_a_running_card_whose_issue_vanished_does_not_raise(board, tmp_path) -> None:
    """A card removed from the project or whose issue was closed between
    ticks must not take the whole tick down with it."""
    card = Card(item_id="I1", number=1, repo="o/r", url="u", title="t",
                body="b", state="CLOSED", fields={"Status": "Running",
                                                  "Repo": "aegis"})
    board["cards"] = [card]
    board["comments"][1] = _marker("t-gone")
    board["quota"] = _quota(10, 10)
    engine = FakeEngine(tasks={})
    out = await tick_mod.run_tick(engine, _cfg(tmp_path),
                                  now="2026-09-25T02:00:00Z")
    assert isinstance(out, str)


@pytest.mark.asyncio
async def test_still_running_card_is_left_alone(board, tmp_path) -> None:
    board["cards"] = [_card(1, "Running")]
    board["comments"][1] = _marker("t-1")
    board["quota"] = _quota(10, 10)
    engine = FakeEngine(tasks={"t-1": {"status": "running", "result": None,
                                       "worker_handle": "w1"}})
    await tick_mod.run_tick(engine, _cfg(tmp_path), now="2026-09-25T02:00:00Z")
    assert board["fields"] == []


@pytest.mark.asyncio
async def test_one_card_per_repo(board, tmp_path) -> None:
    board["cards"] = [_card(1, "Running"), _card(2, "Todo")]
    board["comments"][1] = _marker("t-1")
    board["quota"] = _quota(10, 10)
    engine = FakeEngine(tasks={"t-1": {"status": "running", "result": None,
                                       "worker_handle": "w1"}})
    await tick_mod.run_tick(engine, _cfg(tmp_path), now="2026-09-25T02:00:00Z")
    assert engine.enqueued == []


@pytest.mark.asyncio
async def test_max_in_flight_caps_starts(board, tmp_path) -> None:
    for n in range(1, 5):
        (tmp_path / f"r{n}").mkdir()
        (tmp_path / f"r{n}" / "Makefile").write_text("check:\n\t@echo ok\n")
    SCHEMA.option_ids["Repo"].update({f"r{n}": f"o_r{n}" for n in range(1, 5)})
    board["cards"] = [_card(n, "Todo", repo=f"r{n}") for n in range(1, 5)]
    board["quota"] = _quota(10, 10)
    engine = FakeEngine()
    await tick_mod.run_tick(engine, _cfg(tmp_path, max_in_flight=2),
                            now="2026-09-25T02:00:00Z")
    assert len(engine.enqueued) == 2


@pytest.mark.asyncio
async def test_an_unauthenticated_gh_touches_no_card(board, tmp_path) -> None:
    """Checked once, before any card. A coordinator that cannot write the
    board would otherwise start workers and lose every result — the
    expensive half runs and the recorded half does not."""
    board["cards"] = [_card(1, "Todo")]
    board["quota"] = _quota(10, 10)
    engine = FakeEngine(bash_results={
        "gh auth status": {"exit": 1, "stdout": "not logged in"}})
    out = await tick_mod.run_tick(engine, _cfg(tmp_path),
                                  now="2026-09-25T02:00:00Z")
    assert engine.enqueued == []
    assert board["fields"] == []
    assert "aborted" in out


@pytest.mark.asyncio
async def test_a_gh_token_without_project_scope_aborts(board, tmp_path) -> None:
    board["cards"] = [_card(1, "Todo")]
    board["quota"] = _quota(10, 10)
    engine = FakeEngine(bash_results={
        "gh auth status": {"exit": 0, "stdout": "Token scopes: 'repo', 'gist'"}})
    out = await tick_mod.run_tick(engine, _cfg(tmp_path),
                                  now="2026-09-25T02:00:00Z")
    assert engine.enqueued == []
    assert "project" in out


@pytest.mark.asyncio
async def test_a_dirty_tree_blocks_the_card(board, tmp_path) -> None:
    board["cards"] = [_card(1, "Todo")]
    board["quota"] = _quota(10, 10)
    engine = FakeEngine(bash_results={
        "status --porcelain": {"exit": 0, "stdout": " M src/a.py\n"}})
    await tick_mod.run_tick(engine, _cfg(tmp_path), now="2026-09-25T02:00:00Z")
    assert engine.enqueued == []
    assert (1, "Status", "Blocked") in board["fields"]
    assert "src/a.py" in board["comments"][1]


@pytest.mark.asyncio
async def test_fetch_comment_reads_the_marked_comment_over_gh(tmp_path) -> None:
    """The real read path, unmocked: the board fixture replaces this
    function, so without a test of its own the coordinator's only way of
    recovering a task id would ship uncovered."""
    card = _card(7, "Running")
    body = _marker("t-7")
    payload = json.dumps([{"id": 1, "body": "a human reply"},
                          {"id": 2, "body": body}])
    engine = FakeEngine(bash_results={"issues/7/comments": {"exit": 0,
                                                           "stdout": payload}})
    assert await tick_mod.fetch_comment(engine, card) == body
