"""Every surface shows task, outcome and next (unified-recap spec,
"What each surface shows")."""

from __future__ import annotations

import pytest
from rich.console import Console

from aegis.commands import CommandContext, dispatch
from aegis.fleet.models import CardView
from aegis.fleet.render import render_detail
from aegis.fleet.snapshot import build_snapshot
from aegis.recap import Recap
from aegis.render import render_recap
from aegis.tui.sidebar import Sidebar
from aegis.tui.themes import INK, aegis_colors

from tests.test_fleet_snapshot import FakeManager, FakeSession
from tests.test_fleet_watching import _bridged, _tabs

C = aegis_colors(INK)


def _lines(r) -> list[str]:
    con = Console(record=True, width=100)
    con.print(r)
    return [ln.strip(" ▏│") for ln in con.export_text().splitlines()]


def test_the_turn_recap_puts_the_task_on_its_own_line_after_the_outcome():
    r = Recap(line="Settled the schema.", task="Unify the recap", ok=True)
    lines = _lines(render_recap(r, C))
    out = next(i for i, ln in enumerate(lines) if "Settled the schema." in ln)
    task = next(i for i, ln in enumerate(lines) if "Unify the recap" in ln)
    assert task > out
    assert "Settled the schema." not in lines[task]


def test_the_session_recap_labels_task_outcome_and_next():
    r = Recap(task="the judge", line="the spec", next="the wiring", ok=True)
    text = "\n".join(_lines(render_recap(r, C, session=True)))
    assert "task:" in text and "outcome:" in text and "next:" in text


class _Bridge:
    def __init__(self, recap):
        self._recap = recap

    async def recap(self, handle, *, session_scope=True):
        return self._recap


@pytest.mark.asyncio
async def test_the_recap_command_returns_the_block():
    r = Recap(task="the judge", line="the spec", next="the wiring", ok=True)
    res = await dispatch("/recap", CommandContext(_Bridge(r), "agent-1"))
    assert res.title == r.block
    assert res.effect["session"] is True


def _card(**kw):
    return CardView(handle="h", did="Shipped it.", doing="Running it.", **kw)


def test_the_detail_orders_task_now_did_next():
    plain = render_detail(_card(task="Unify the recap", next="Wire F3"), C, 90, 0).plain
    idx = [plain.index(h) for h in ("TASK", "NOW", "DID", "NEXT")]
    assert idx == sorted(idx)
    assert "Unify the recap" in plain and "Wire F3" in plain


def test_the_detail_omits_empty_task_and_next():
    plain = render_detail(_card(), C, 90, 0).plain
    assert "TASK" not in plain and "NEXT" not in plain


def test_the_snapshot_carries_task_next_and_the_mid_turn_outcome():
    s = FakeSession("alpha", did="Shipped it.")
    s._last_recap_task = "Unify the recap"
    s._last_recap_next = "Wire F3"
    s.fleet_recap = Recap(line="Running the suite.", ok=True)
    (card,) = build_snapshot(FakeManager([s]), now=1000.0).cards
    assert card.task == "Unify the recap"
    assert card.next == "Wire F3"
    assert card.doing == "Running the suite."


def test_the_first_turn_takes_its_task_from_the_mid_turn_recap():
    s = FakeSession("alpha")
    s.fleet_recap = Recap(line="Running the suite.", task="Unify the recap", ok=True)
    (card,) = build_snapshot(FakeManager([s]), now=1000.0).cards
    assert card.task == "Unify the recap"


def test_the_recap_effect_reads_its_session_key():
    from dataclasses import asdict

    from aegis.commands import CommandResult
    from aegis.tui.pane import ConversationPane

    class _Pane:
        def __init__(self):
            self.puts = []

        def _put_recap(self, recap, *, session=False, at_idx=None):
            self.puts.append(session)

    pane = _Pane()
    r = Recap(line="x", ok=True)
    for flag in (False, True):
        eff = {"kind": "recap", "recap": asdict(r), "session": flag}
        ConversationPane._apply_command_result(
            pane, CommandResult(True, r.block, r.footer, effect=eff), 80
        )
    ConversationPane._apply_command_result(
        pane,
        CommandResult(True, r.block, r.footer, effect={"kind": "recap", "recap": asdict(r)}),
        80,
    )
    assert pane.puts == [False, True, True]


async def test_the_sidebar_now_line_reads_the_mid_turn_outcome(tmp_path):
    app = _bridged(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await _tabs(app, pilot, 1)
        await pilot.press("f3")
        await pilot.pause()
        pane = app._panes[0]
        core = pane._core
        bar = pane.query_one("#sidebar", Sidebar)
        (cb,) = core._fleet_watchers
        recap = Recap(line="Wiring the watcher.", ok=True)
        core.fleet_recap = recap
        cb(core, recap)
        assert bar._model.now_line == "Wiring the watcher."
