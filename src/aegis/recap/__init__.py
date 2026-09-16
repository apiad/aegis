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

# Measured 2026-09-16: the prefix floor is ~1,027 input tokens and a full
# window costs ~546 more, so the window is calderilla and gets sized for
# relevance rather than thrift. Squeezing it to ~1,135 total produced
# terser lines that named no files.
IN_FLIGHT_WINDOW = dict(max_turns=2, budget_tokens=2_500, item_chars=240)


class TurnRecap(BaseModel):
    line: str = Field(
        description="ONE line, past tense, concrete. Name "
        "files and counts. No preamble."
    )


class SessionRecap(BaseModel):
    building: str = Field(description="what the session is working toward")
    done: str = Field(description="what has actually landed")
    remaining: str = Field(description="what is left")


class FleetRecap(BaseModel):
    done: str = Field(
        description="ONE line, past tense: the last thing that "
        "actually landed. Name files and counts. No preamble."
    )
    doing: str = Field(
        description="ONE line, present tense: what the turn "
        "currently running is working on."
    )


@dataclass(frozen=True)
class Recap:
    """One recap, and what it cost."""

    line: str = ""
    building: str = ""
    done: str = ""
    doing: str = ""
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
        if not (self.building or self.done or self.doing or self.remaining):
            return ""
        return "\n".join(
            x
            for x in (
                f"- **building:** {self.building}" if self.building else "",
                f"- **done:** {self.done}" if self.done else "",
                f"- **doing:** {self.doing}" if self.doing else "",
                f"- **remaining:** {self.remaining}" if self.remaining else "",
            )
            if x
        )

    @property
    def footer(self) -> str:
        """The price, shown because a recap is a paid call."""
        bits = [
            b
            for b in (
                self.model,
                f"{self.duration_ms / 1000:.1f}s" if self.duration_ms else "",
                f"${self.cost_usd:.4f}" if self.cost_usd else "",
                self.header,
            )
            if b
        ]
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

_IN_FLIGHT_SYSTEM = (
    "You say where a coding agent stands in the middle of a turn that has "
    "not finished, for an operator glancing at a dashboard. Two fields: the "
    "last thing that actually landed (past tense), and what the running "
    "turn is doing now (present tense). Prefer the FACTS block over the "
    "agent's own narration — the agent describes what it means to do; the "
    "facts say what landed. Name files and counts. No preamble, no praise."
)


async def _one(
    schema, system, *, replay, facts, driver, agent, cwd, window_opts
) -> Recap:
    header = ""
    try:
        # Inside the try, not before it: best-effort by contract means a
        # replay that cannot be assembled is a missing recap, not a raise.
        # The in-flight caller hands over a live event list, a newer kind of
        # input than the persisted replays the turn recap reads.
        window = assemble(replay, **window_opts)
        header = window.header
        gen = await driver.generate_detailed(
            agent,
            cwd,
            schema,
            system,
            f"--- conversation ({window.header or 'no turns yet'}) ---\n"
            f"{window.text}\n--- end ---",
            render_facts(facts),
        )
    except Exception as e:  # noqa: BLE001
        return Recap(header=header, error=f"{type(e).__name__}: {e}")
    if gen is None or gen.value is None:
        return Recap(
            header=window.header,
            model=getattr(gen, "model", ""),
            duration_ms=getattr(gen, "duration_ms", 0),
            cost_usd=getattr(gen, "cost_usd", 0.0),
            error="the model returned nothing usable",
        )
    v = gen.value
    return Recap(
        line=getattr(v, "line", ""),
        building=getattr(v, "building", ""),
        done=getattr(v, "done", ""),
        doing=getattr(v, "doing", ""),
        remaining=getattr(v, "remaining", ""),
        header=window.header,
        model=gen.model,
        duration_ms=gen.duration_ms,
        cost_usd=gen.cost_usd,
        ok=True,
    )


async def recap_turn(*, replay, facts: TurnFacts, driver, agent, cwd: str) -> Recap:
    """One line about the turn that just ended."""
    return await _one(
        TurnRecap,
        _TURN_SYSTEM,
        replay=replay,
        facts=facts,
        driver=driver,
        agent=agent,
        cwd=cwd,
        window_opts=TURN_WINDOW,
    )


async def recap_session(*, replay, facts: TurnFacts, driver, agent, cwd: str) -> Recap:
    """A short block about where the session stands."""
    return await _one(
        SessionRecap,
        _SESSION_SYSTEM,
        replay=replay,
        facts=facts,
        driver=driver,
        agent=agent,
        cwd=cwd,
        window_opts={},
    )


async def recap_in_flight(
    *, replay, facts: TurnFacts, driver, agent, cwd: str
) -> Recap:
    """Two lines about a turn still running: what landed, what it is doing."""
    return await _one(
        FleetRecap,
        _IN_FLIGHT_SYSTEM,
        replay=replay,
        facts=facts,
        driver=driver,
        agent=agent,
        cwd=cwd,
        window_opts=IN_FLIGHT_WINDOW,
    )


async def _resolve(
    *, state_dir, log_id: str, agent, agents: dict, root, unattended: bool = False
):
    """Driver, billing profile and transcript — or a ``Recap`` saying why not.

    Mirrors ``btw.side_note_for`` exactly, including reading the log off
    the event loop — a 24MB transcript takes 0.65s warm, far too much to
    spend on the UI thread.
    """
    import asyncio

    from aegis.btw import generation_agent
    from aegis.drivers import get_driver
    from aegis.state.session_log import replay_events

    # The session's config root, not the cwd: a daemon started from a
    # directory with its own .aegis.yaml would otherwise bill elsewhere.
    gen_agent, _unset = generation_agent(agent, agents, root)
    if unattended and _unset:
        # The turn recap falls back to the session's own model and says so.
        # A recurring call nobody asked for must not bill Opus silently.
        return Recap(
            error="set text_generation: to bill the mid-turn recap to a cheap profile"
        )
    try:
        driver = get_driver(gen_agent.harness)
    except KeyError:
        return Recap(error=f"unknown harness: {gen_agent.harness!r}")
    if not getattr(driver, "supports_oneshot", False):
        return Recap(
            error=f"the {gen_agent.harness} driver cannot do "
            f"one-shot generation — point "
            f"`text_generation:` at one that can"
        )
    try:
        replay = await asyncio.to_thread(replay_events, state_dir, log_id)
    except Exception as e:  # noqa: BLE001
        return Recap(error=f"could not read the transcript: {e}")
    return driver, gen_agent, replay


async def recap_for(
    *,
    state_dir,
    log_id: str,
    facts: TurnFacts,
    agent,
    agents: dict,
    cwd: str,
    session_scope: bool,
    root=None,
) -> Recap:
    """Resolve driver + billing profile + transcript, then ask."""
    got = await _resolve(
        state_dir=state_dir, log_id=log_id, agent=agent, agents=agents, root=root
    )
    if isinstance(got, Recap):
        return got
    driver, gen_agent, replay = got
    fn = recap_session if session_scope else recap_turn
    return await fn(replay=replay, facts=facts, driver=driver, agent=gen_agent, cwd=cwd)


async def recap_in_flight_for(
    *,
    state_dir,
    log_id: str,
    facts: TurnFacts,
    agent,
    agents: dict,
    cwd: str,
    root=None,
) -> Recap:
    """``recap_for`` for a turn still running: same billing, same transcript.

    The log is appended as the turn streams, so a mid-turn read sees the
    turn up to now.
    """
    got = await _resolve(
        state_dir=state_dir,
        log_id=log_id,
        agent=agent,
        agents=agents,
        root=root,
        unattended=True,
    )
    if isinstance(got, Recap):
        return got
    driver, gen_agent, replay = got
    return await recap_in_flight(
        replay=replay, facts=facts, driver=driver, agent=gen_agent, cwd=cwd
    )
