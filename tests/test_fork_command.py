"""The /fork slash command — branch the current pane's conversation.

A slash command is handled by aegis itself rather than sent to the
agent as a turn, so the pane is idle when it runs. That is what makes
self-fork legal here and not over MCP, where an agent calling
aegis_fork is by definition mid-turn.
"""
from __future__ import annotations

from aegis.commands import CommandContext, dispatch


class FakeBridge:
    def __init__(self, *, refuse: str | None = None):
        self.forked: list = []
        self._refuse = refuse

    def list_agents(self):
        return ["default", "opus"]

    def list_sessions(self):
        return []

    async def fork(self, target, *, prompt=None, slug=None,
                   model=None, effort=None, forked_by=None):
        if self._refuse:
            raise ValueError(self._refuse)
        self.forked.append(
            {"target": target, "prompt": prompt, "slug": slug,
             "model": model, "effort": effort, "forked_by": forked_by})
        return "branch-1"


def _ctx(bridge=None):
    return CommandContext(bridge=bridge or FakeBridge(), handle="me")


async def test_bare_fork_branches_the_current_pane_with_no_prompt():
    b = FakeBridge()
    res = await dispatch("/fork", _ctx(b))
    assert res.ok
    assert b.forked[0]["target"] == "me"
    assert b.forked[0]["prompt"] is None


async def test_fork_reports_the_new_handle():
    res = await dispatch("/fork", _ctx())
    assert "branch-1" in res.title


async def test_fork_passes_a_greedy_prompt():
    """The divergence is free text and must survive the spaces in it."""
    b = FakeBridge()
    await dispatch("/fork try the Line API approach instead", _ctx(b))
    assert b.forked[0]["prompt"] == "try the Line API approach instead"


async def test_fork_passes_model_and_effort_overrides():
    b = FakeBridge()
    await dispatch("/fork --model haiku --effort max grind this out",
                   _ctx(b))
    assert b.forked[0]["model"] == "haiku"
    assert b.forked[0]["effort"] == "max"
    assert b.forked[0]["prompt"] == "grind this out"


async def test_fork_surfaces_a_refusal_instead_of_raising():
    """The guard's reasons are the whole point — a refusal that reaches
    the user as a traceback teaches nothing about how long to wait."""
    b = FakeBridge(refuse="'me' is mid-turn (a fork would branch from a "
                          "dangling tool call — wait for the turn to finish)")
    res = await dispatch("/fork", _ctx(b))
    assert not res.ok
    assert "mid-turn" in res.title
