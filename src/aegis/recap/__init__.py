"""`/recap` — where this turn, or this session, actually stands.

One schema, one prompt, three windows. The turn recap, the mid-turn recap
and `/recap` answer the same question — what the session is working
toward, what just came of it, what is left — at three window sizes, so
they share ``StandingRecap`` and ``SYSTEM``; only the window changes, and
the mid-turn call adds that the turn is still running. The prompt speaks
at the level of intent and outcome because the earlier ones asked for
files and counts and got an inventory (see the unified-recap spec,
2026-09-17).

Gating lives in ``aegis.recap.gate``, not here — this module only knows
how to ask. Best-effort by contract, like ``titlegen``: every failure
comes back as a ``Recap`` with ``ok=False``, never as an exception.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from aegis.btw.window import assemble
from aegis.digest.models import TurnFacts
from aegis.digest.render import render_facts

# Measured 2026-08-26: the window is NOT the cost — the prefix is (21,445
# tokens before --setting-sources "", 6,926 after). Sized for relevance:
# three turns keep the goal in view, measured at $0.016 per call
# (2026-09-17 abstract-recap probe).
TURN_WINDOW = dict(max_turns=3, budget_tokens=3_000, item_chars=300)

# Sized for relevance rather than thrift. Measured 2026-09-16 on a real
# 61-turn transcript: this window is ~2,600 tokens, and the whole call cost
# 4,902 input tokens / $0.0162 from an empty directory ($0.0073 once the
# prefix is cached), so the window is about half the input and most of a
# cached call's cost.
IN_FLIGHT_WINDOW = dict(max_turns=2, budget_tokens=2_500, item_chars=240)

# `/recap` is not the whole conversation: eight turns, measured at $0.035.
SESSION_WINDOW = dict(max_turns=8, budget_tokens=8_000, item_chars=300)


class StandingRecap(BaseModel):
    task: str = Field(
        description="What the session is working toward, as a goal a person "
        "would name in one short phrase. Not a file, not a command."
    )
    outcome: str = Field(
        description="ONE sentence, at most 20 words: what was just solved, "
        "decided, delivered or learned, at the level of the goal. No file "
        "names, hashes, task ids, test counts or tool names unless that name "
        "is the subject itself."
    )
    next: str = Field(
        description="ONE short sentence: what is left, or what the session is "
        "waiting for. Empty if nothing."
    )
    attention: Literal["needs_input", "error", "review", "waiting", "done"] = Field(
        description="needs_input: the turn ended on a question or a decision "
        "for the operator. error: something failed. review: it presents "
        "something for the operator to read, without waiting on it. waiting: "
        "it waits on a monitor, a queue, a subagent or CI, not on the "
        "operator. done: it reports finished work and needs nothing."
    )


@dataclass(frozen=True)
class Recap:
    """One recap, and what it cost."""

    # The outcome. Named `line` because persisted `RecapNote`s already are.
    line: str = ""
    task: str = ""
    next: str = ""
    attention: str = ""
    header: str = ""
    model: str = ""
    duration_ms: int = 0
    cost_usd: float = 0.0
    ok: bool = False
    error: str = ""

    @property
    def text(self) -> str:
        """The outcome: the body of a turn or mid-turn recap."""
        return self.line

    @property
    def block(self) -> str:
        """`/recap`'s body: a markdown list, because Rich's Markdown joins
        single newlines into one paragraph."""
        return "\n".join(
            f"- **{name}:** {value}"
            for name, value in (
                ("task", self.task),
                ("outcome", self.line),
                ("next", self.next),
            )
            if value
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


SYSTEM = (
    "You tell an operator, glancing at a dashboard, where a coding agent's "
    "session stands. Speak at the level of intent and outcome: the problem "
    "being solved, what got solved or decided, what is left. Never list "
    "files, commits, hashes, task ids, ports, test counts, finding counts or "
    "tool calls, and avoid numbers unless the number is the point (a budget "
    "that ran out, a deadline). The FACTS block is only a guard against "
    "claiming work that did not happen; do not report its contents. Prefer "
    "what actually happened over what the agent said it would do. "
    "LANGUAGE: write every field in the language of the operator's own "
    "messages (the lines marked as the user), even when the agent answers in "
    "another language. `task` names the goal, never a state like waiting. "
    "`outcome` is at most 20 words. No preamble, no praise. A question to the "
    "operator is needs_input even when the turn also landed work."
)

IN_FLIGHT_ADDENDUM = (
    " The turn is still running: `outcome` is what it is doing right now, "
    "in the present tense."
)


async def _one(
    system,
    *,
    replay,
    facts,
    driver,
    agent,
    cwd,
    window_opts,
    previous_task: str = "",
    on_driver=None,
) -> Recap:
    header = ""
    try:
        # Inside the try, not before it: best-effort by contract means a
        # replay that cannot be assembled is a missing recap, not a raise.
        # The in-flight caller hands over a live event list, a newer kind of
        # input than the persisted replays the turn recap reads.
        window = assemble(replay, **window_opts)
        header = window.header
        instructions = [
            f"--- conversation ({window.header or 'no turns yet'}) ---\n"
            f"{window.text}\n--- end ---",
            render_facts(facts),
        ]
        if previous_task:
            # Measured: without it, `task` wanders between phrasings of the
            # same goal on consecutive turns.
            instructions.append(
                f"Previous task: {previous_task} — keep it unless the goal changed."
            )
        if on_driver is not None:
            # No await between this and the call: a cancel from here on
            # lands inside the driver, where a started call is paid for.
            on_driver()
        gen = await driver.generate_detailed(
            agent, cwd, StandingRecap, system, *instructions
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
        line=v.outcome,
        task=v.task,
        next=v.next,
        attention=v.attention,
        header=window.header,
        model=gen.model,
        duration_ms=gen.duration_ms,
        cost_usd=gen.cost_usd,
        ok=True,
    )


async def recap_turn(
    *, replay, facts: TurnFacts, driver, agent, cwd: str, previous_task: str = ""
) -> Recap:
    """Where the session stands after the turn that just ended."""
    return await _one(
        SYSTEM,
        replay=replay,
        facts=facts,
        driver=driver,
        agent=agent,
        cwd=cwd,
        window_opts=TURN_WINDOW,
        previous_task=previous_task,
    )


async def recap_session(
    *, replay, facts: TurnFacts, driver, agent, cwd: str, previous_task: str = ""
) -> Recap:
    """Where the session stands, over a wider window, for `/recap`."""
    return await _one(
        SYSTEM,
        replay=replay,
        facts=facts,
        driver=driver,
        agent=agent,
        cwd=cwd,
        window_opts=SESSION_WINDOW,
        previous_task=previous_task,
    )


async def recap_in_flight(
    *,
    replay,
    facts: TurnFacts,
    driver,
    agent,
    cwd: str,
    previous_task: str = "",
    on_driver=None,
) -> Recap:
    """Where a turn still running stands: its outcome is what it is doing.

    ``on_driver`` is called just before the driver is, so a caller that
    cancels can tell a paid call from one that never started.
    """
    return await _one(
        SYSTEM + IN_FLIGHT_ADDENDUM,
        replay=replay,
        facts=facts,
        driver=driver,
        agent=agent,
        cwd=cwd,
        window_opts=IN_FLIGHT_WINDOW,
        previous_task=previous_task,
        on_driver=on_driver,
    )


async def _resolve(
    *,
    state_dir,
    log_id: str,
    agent,
    agents: dict | None,
    root,
    unattended: bool = False,
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
    gen_agent, _unset = generation_agent(agent, agents or {}, root)
    if unattended and _unset:
        # The turn recap falls back to the session's own model and says so.
        # A recurring call nobody asked for must not bill Opus silently.
        return Recap(
            error=(
                "text_generation: must name a configured agent profile to bill "
                "the mid-turn recap (it is unset, or names no profile)"
            )
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
    previous_task: str = "",
) -> Recap:
    """Resolve driver + billing profile + transcript, then ask."""
    got = await _resolve(
        state_dir=state_dir, log_id=log_id, agent=agent, agents=agents, root=root
    )
    if isinstance(got, Recap):
        return got
    driver, gen_agent, replay = got
    fn = recap_session if session_scope else recap_turn
    return await fn(
        replay=replay,
        facts=facts,
        driver=driver,
        agent=gen_agent,
        cwd=cwd,
        previous_task=previous_task,
    )


async def recap_in_flight_for(
    *,
    state_dir,
    log_id: str,
    facts: TurnFacts,
    agent,
    agents: dict | None,
    cwd: str,
    root=None,
    previous_task: str = "",
    on_driver=None,
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
        replay=replay,
        facts=facts,
        driver=driver,
        agent=gen_agent,
        cwd=cwd,
        previous_task=previous_task,
        on_driver=on_driver,
    )
