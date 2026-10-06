"""Consecutive AgentPlan revisions collapse to one transcript block.

The plan is a mutating object: every TaskCreate / TaskUpdate / TaskList
re-emits the whole thing, so a 21-task plan arrives as ~50 cumulative
events. Appending each one would trade N anonymous tool rows for N plan
blocks — the same noise wearing a hat.
"""
import pytest

from aegis.events import AgentPlan, AssistantText, PlanEntry, ToolUse
from aegis.tui.pane import fold_plan_events


def p(*pairs, parent=None):
    return AgentPlan(
        entries=tuple(PlanEntry(content=c, status=s) for c, s in pairs),
        parent_tool_use_id=parent)


def test_consecutive_plans_collapse_to_the_latest():
    out = fold_plan_events([p(("a", "pending")), p(("a", "in_progress")),
                            p(("a", "completed"))])
    assert len(out) == 1
    assert out[0].entries[0].status == "completed"


def test_a_plan_after_other_events_still_replaces_the_earlier_one():
    out = fold_plan_events([p(("a", "pending")),
                            AssistantText(text="thinking"),
                            p(("a", "completed"))])
    assert [type(e).__name__ for e in out] == ["AgentPlan", "AssistantText"]
    assert out[0].entries[0].status == "completed"


def test_the_plan_keeps_its_original_position():
    """It replaces in place rather than jumping to the end: the strip is
    the live surface, the transcript block is a record of where the plan
    first appeared."""
    out = fold_plan_events([AssistantText(text="one"), p(("a", "pending")),
                            AssistantText(text="two"), p(("a", "completed"))])
    assert [type(e).__name__ for e in out] == [
        "AssistantText", "AgentPlan", "AssistantText"]


def test_subagent_plans_are_kept_separate_from_the_parent_plan():
    out = fold_plan_events([p(("a", "pending")),
                            p(("x", "pending"), parent="tool_1"),
                            p(("a", "completed"))])
    plans = [e for e in out if isinstance(e, AgentPlan)]
    assert len(plans) == 2
    assert {pl.parent_tool_use_id for pl in plans} == {None, "tool_1"}


def test_two_subagents_each_keep_their_own_block():
    out = fold_plan_events([p(("x", "pending"), parent="t1"),
                            p(("y", "pending"), parent="t2"),
                            p(("x", "completed"), parent="t1")])
    plans = [e for e in out if isinstance(e, AgentPlan)]
    assert len(plans) == 2
    assert plans[0].entries[0].status == "completed"


def test_non_plan_events_are_untouched():
    evs = [AssistantText(text="a"),
           ToolUse(name="Bash", summary="ls", tool_call_id="t1")]
    assert fold_plan_events(evs) == evs


def test_an_empty_stream_is_empty():
    assert fold_plan_events([]) == []


def test_a_realistic_run_collapses_fifty_revisions_to_one_block():
    """The actual shape this exists for."""
    evs = []
    for i in range(50):
        done = [("t%d" % j, "completed") for j in range(i)]
        rest = [("t%d" % j, "pending") for j in range(i, 50)]
        evs.append(p(*(done + rest)))
    out = fold_plan_events(evs)
    assert len(out) == 1
    assert out[0].entries[-1].content == "t49"


# -- live in-place replacement ---------------------------------------

class _Gated:
    def __init__(self):
        self.sent: list[str] = []
        self.started = self.closed = False

    async def start(self):
        self.started = True

    async def send(self, text):
        self.sent.append(text)

    async def events(self):
        from aegis.events import Result
        yield Result(duration_ms=1, is_error=False, usage=None)

    async def close(self):
        self.closed = True


class _FakeMCP:
    url = "http://127.0.0.1:0/mcp/"

    def bind(self, bridge):
        pass

    async def start(self):
        pass

    async def stop(self):
        pass


def _app():
    from aegis.config import Agent
    from aegis.tui.app import AegisApp

    agent = Agent(harness="claude-code", model="opus", effort="high",
                  permission="auto")
    return AegisApp({"default": agent}, "default",
                    lambda a, u, h: _Gated(), _FakeMCP())


@pytest.mark.asyncio
async def test_live_plan_revisions_mutate_one_block():
    """Fifty revisions must leave one block in the transcript, not fifty."""
    async with _app().run_test() as pilot:
        pane = pilot.app._panes[0]
        before = len(pane._history)
        for i in range(50):
            pane._core._fire_event(p(("a", "completed" if i else "pending"),
                                     ("b", "in_progress")))
        await pilot.pause()
        added = len(pane._history) - before
        assert added == 1, f"{added} blocks for 50 revisions"


@pytest.mark.asyncio
async def test_the_surviving_block_shows_the_latest_revision():
    async with _app().run_test() as pilot:
        pane = pilot.app._panes[0]
        pane._core._fire_event(p(("a", "pending"), ("b", "pending")))
        pane._core._fire_event(p(("a", "completed"), ("b", "in_progress")))
        await pilot.pause()
        rec = pane._history[-1]
        assert "1/2" in rec.renderable.plain


@pytest.mark.asyncio
async def test_a_subagent_plan_gets_its_own_block():
    async with _app().run_test() as pilot:
        pane = pilot.app._panes[0]
        before = len(pane._history)
        pane._core._fire_event(p(("a", "pending")))
        pane._core._fire_event(p(("x", "pending"), parent="tool_1"))
        pane._core._fire_event(p(("a", "completed")))
        await pilot.pause()
        assert len(pane._history) - before == 2
