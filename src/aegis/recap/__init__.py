"""`/recap` — where this turn, or this session, actually stands.

Two schemas rather than one with optional fields: the automatic recap is
one line about a turn, `/recap` is a short block about a session, and a
schema serving two masters degrades both.

Gating lives in ``aegis.recap.gate``, not here — this module only knows
how to ask. Best-effort by contract, like ``titlegen``: every failure
comes back as a ``Recap`` with ``ok=False``, never as an exception.
"""
from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from aegis.btw.window import assemble
from aegis.digest.models import TurnFacts
from aegis.digest.render import render_facts

# Measured 2026-08-26: the window is NOT the cost — the prefix is (21,445
# tokens before --setting-sources "", 6,926 after). A tight window buys
# little, so this is sized for relevance rather than thrift: one turn is
# what an end-of-turn line is about.
TURN_WINDOW = dict(max_turns=1, budget_tokens=2_000, item_chars=200)


class TurnRecap(BaseModel):
    line: str = Field(description="ONE line, past tense, concrete. Name "
                                  "files and counts. No preamble.")


class SessionRecap(BaseModel):
    building: str = Field(description="what the session is working toward")
    done: str = Field(description="what has actually landed")
    remaining: str = Field(description="what is left")


@dataclass(frozen=True)
class Recap:
    """One recap, and what it cost."""
    line: str = ""
    building: str = ""
    done: str = ""
    remaining: str = ""
    header: str = ""
    model: str = ""
    duration_ms: int = 0
    cost_usd: float = 0.0
    ok: bool = False
    error: str = ""

    @property
    def text(self) -> str:
        """The rendered body.

        The session form is a **markdown list**, not newline-joined lines.
        It is rendered through ``rich.markdown.Markdown``, which collapses
        single newlines into spaces — so the plain-join version drew as one
        run-on paragraph ("building: x done: y remaining: z") while every
        substring assertion about it still passed. Caught by looking at the
        rendered block rather than at ``text``.
        """
        if self.line:
            return self.line
        if not (self.building or self.done or self.remaining):
            return ""
        return "\n".join(x for x in (
            f"- **building:** {self.building}" if self.building else "",
            f"- **done:** {self.done}" if self.done else "",
            f"- **remaining:** {self.remaining}" if self.remaining else "",
        ) if x)

    @property
    def footer(self) -> str:
        """The price, shown because a recap is a paid call."""
        bits = [b for b in (
            self.model,
            f"{self.duration_ms / 1000:.1f}s" if self.duration_ms else "",
            f"${self.cost_usd:.4f}" if self.cost_usd else "",
            self.header,
        ) if b]
        return " · ".join(bits)


_TURN_SYSTEM = (
    "You write a single line saying what a coding agent's last turn did. "
    "Past tense, concrete, no preamble, no praise. Prefer the FACTS block "
    "over the agent's own narration — the agent describes what it meant "
    "to do; the facts say what landed. Name files and counts."
)

_SESSION_SYSTEM = (
    "You summarize where a coding session stands, for an operator "
    "returning to it. Three short fields: what is being built, what has "
    "landed, what is left. Prefer the FACTS block over the agent's own "
    "narration. No preamble, no praise."
)


async def _one(schema, system, *, replay, facts, driver, agent, cwd,
               window_opts) -> Recap:
    window = assemble(replay, **window_opts)
    try:
        gen = await driver.generate_detailed(
            agent, cwd, schema, system,
            f"--- conversation ({window.header or 'no turns yet'}) ---\n"
            f"{window.text}\n--- end ---",
            render_facts(facts))
    except Exception as e:                                    # noqa: BLE001
        return Recap(header=window.header,
                     error=f"{type(e).__name__}: {e}")
    if gen is None or gen.value is None:
        return Recap(header=window.header,
                     model=getattr(gen, "model", ""),
                     duration_ms=getattr(gen, "duration_ms", 0),
                     cost_usd=getattr(gen, "cost_usd", 0.0),
                     error="the model returned nothing usable")
    v = gen.value
    return Recap(
        line=getattr(v, "line", ""),
        building=getattr(v, "building", ""),
        done=getattr(v, "done", ""),
        remaining=getattr(v, "remaining", ""),
        header=window.header, model=gen.model,
        duration_ms=gen.duration_ms, cost_usd=gen.cost_usd, ok=True)


async def recap_turn(*, replay, facts: TurnFacts, driver, agent,
                     cwd: str) -> Recap:
    """One line about the turn that just ended."""
    return await _one(TurnRecap, _TURN_SYSTEM, replay=replay, facts=facts,
                      driver=driver, agent=agent, cwd=cwd,
                      window_opts=TURN_WINDOW)


async def recap_session(*, replay, facts: TurnFacts, driver, agent,
                        cwd: str) -> Recap:
    """A short block about where the session stands."""
    return await _one(SessionRecap, _SESSION_SYSTEM, replay=replay,
                      facts=facts, driver=driver, agent=agent, cwd=cwd,
                      window_opts={})


async def recap_for(*, state_dir, log_id: str, facts: TurnFacts, agent,
                    agents: dict, cwd: str,
                    session_scope: bool) -> Recap:
    """Resolve driver + billing profile + transcript, then ask.

    Mirrors ``btw.side_note_for`` exactly, including reading the log off
    the event loop — a 24MB transcript takes 0.65s warm, far too much to
    spend on the UI thread.
    """
    import asyncio

    from aegis.btw import generation_agent
    from aegis.drivers import get_driver
    from aegis.state.session_log import replay_events

    gen_agent, _unset = generation_agent(agent, agents)
    try:
        driver = get_driver(gen_agent.harness)
    except KeyError:
        return Recap(error=f"unknown harness: {gen_agent.harness!r}")
    if not getattr(driver, "supports_oneshot", False):
        return Recap(error=f"the {gen_agent.harness} driver cannot do "
                           f"one-shot generation — point "
                           f"`text_generation:` at one that can")
    try:
        replay = await asyncio.to_thread(replay_events, state_dir, log_id)
    except Exception as e:                                    # noqa: BLE001
        return Recap(error=f"could not read the transcript: {e}")
    fn = recap_session if session_scope else recap_turn
    return await fn(replay=replay, facts=facts, driver=driver,
                    agent=gen_agent, cwd=cwd)
