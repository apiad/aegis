"""Whether an armed `/loop` continues — decided from facts, not from the
agent's self-report.

The agent deciding whether the agent is done is the agent grading its own
homework, from inside the tunnel it has been in for N turns. On
2026-07-30 that cost a whole night of autonomy: a loop reaped at
iteration 1 of 20 with the model-manager UI, the download/load API and
the docs unbuilt. openai/codex#27352 is the same failure in another
harness — "Codex CLI can prematurely mark a turn as complete after the
assistant emits a commentary/progress message that promises a next
action". No terminal coding harness surveyed has an external judge; the
self-report is the known weak point everywhere.

**Best-effort, and its failure mode is CONTINUE.** The iteration cap is
what bounds runaway; a failed API call must never silently end a night of
work.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from aegis.btw.window import assemble
from aegis.digest.models import TurnFacts
from aegis.digest.render import render_facts

# Two turns, because the judge is asking "is this finished?" and the turn
# before the last one is often where the substance is.
JUDGE_WINDOW = dict(max_turns=2, budget_tokens=4_000, item_chars=300)

VERDICTS = ("continue", "done", "stuck")


class LoopVerdict(BaseModel):
    verdict: Literal["continue", "done", "stuck"]
    reason: str = Field(description="one sentence, addressed to the "
                                    "operator")
    addendum: str = Field(default="", description="only when continuing: "
                                                  "what REMAINS and what "
                                                  "not to redo. Never "
                                                  "restate the goal.")


@dataclass(frozen=True)
class Judgement:
    """One verdict, and what it cost. ``verdict`` defaults to continue so
    that every failure path is already the safe one."""
    verdict: str = "continue"
    reason: str = ""
    addendum: str = ""
    model: str = ""
    duration_ms: int = 0
    cost_usd: float = 0.0
    ok: bool = False
    error: str = ""


_SYSTEM = (
    "You decide whether a coding agent's looping instruction is finished. "
    "You are OUTSIDE the work, which is the point: the agent has been in "
    "this task for many turns and consistently underestimates what "
    "remains.\n\n"
    "Read the instruction at its WIDEST sensible reading. 'Wire it up' "
    "means the system works end to end and the operator can USE it — not "
    "that one component's wires are connected. Infrastructure with no "
    "user-visible surface is half a job.\n\n"
    "Weigh the FACTS over the agent's narration: the agent says what it "
    "meant to do; the facts say what landed.\n\n"
    "verdict=done only if an operator could sit down and use the result. "
    "verdict=stuck if turns are passing with nothing landing. "
    "Otherwise continue, and put what REMAINS in addendum — never restate "
    "the goal, which is delivered verbatim anyway."
)


def _clean(raw) -> tuple[str, str, str]:
    """(verdict, reason, addendum), normalised.

    An invented verdict becomes ``continue``: the model is not allowed to
    end a loop by inventing a word, and continuing is the safe direction.
    """
    verdict = getattr(raw, "verdict", "")
    if verdict not in VERDICTS:
        verdict = "continue"
    addendum = getattr(raw, "addendum", "") if verdict == "continue" else ""
    return verdict, getattr(raw, "reason", ""), addendum


async def judge(*, instruction: str, iteration: int, max_iterations: int,
                facts: TurnFacts, still_streak: int, advisory: str,
                replay, driver, agent, cwd: str) -> Judgement:
    """One call. Any failure returns a continuing Judgement."""
    window = assemble(replay, **JUDGE_WINDOW)
    state = [f"The looping instruction, verbatim: {instruction}",
             f"This is iteration {iteration} of {max_iterations}.",
             f"Consecutive turns with no commits, no files written and no "
             f"plan movement: {still_streak}."]
    if advisory:
        # Presented as a CLAIM, never as an instruction. This is the whole
        # inversion: aegis_loop_stop is advisory now.
        state.append(f"The agent asked to stop. Its claim, which you are "
                     f"free to reject: {advisory}")
    try:
        gen = await driver.generate_detailed(
            agent, cwd, LoopVerdict, _SYSTEM,
            f"--- conversation ({window.header or 'no turns yet'}) ---\n"
            f"{window.text}\n--- end ---",
            render_facts(facts),
            "\n".join(state))
    except Exception as e:                                    # noqa: BLE001
        return Judgement(error=f"{type(e).__name__}: {e}")
    if gen is None or gen.value is None:
        return Judgement(model=getattr(gen, "model", ""),
                         duration_ms=getattr(gen, "duration_ms", 0),
                         cost_usd=getattr(gen, "cost_usd", 0.0),
                         error="the model returned nothing usable")
    verdict, reason, addendum = _clean(gen.value)
    return Judgement(verdict=verdict, reason=reason, addendum=addendum,
                     model=gen.model, duration_ms=gen.duration_ms,
                     cost_usd=gen.cost_usd, ok=True)


async def judge_for(*, state_dir, log_id: str, instruction: str,
                    iteration: int, max_iterations: int, facts: TurnFacts,
                    still_streak: int, advisory: str, agent, agents: dict,
                    cwd: str) -> Judgement:
    """Resolve driver + billing profile + transcript, then judge.

    Mirrors ``btw.side_note_for``. Note what a resolution failure returns:
    a Judgement whose verdict is ``continue``. A misconfigured
    ``text_generation:`` must not be able to end loops.
    """
    import asyncio

    from aegis.btw import generation_agent
    from aegis.drivers import get_driver
    from aegis.state.session_log import replay_events

    gen_agent, _unset = generation_agent(agent, agents)
    try:
        driver = get_driver(gen_agent.harness)
    except KeyError:
        return Judgement(error=f"unknown harness: {gen_agent.harness!r}")
    if not getattr(driver, "supports_oneshot", False):
        return Judgement(error=f"the {gen_agent.harness} driver cannot do "
                               f"one-shot generation — point "
                               f"`text_generation:` at one that can")
    try:
        replay = await asyncio.to_thread(replay_events, state_dir, log_id)
    except Exception as e:                                    # noqa: BLE001
        return Judgement(error=f"could not read the transcript: {e}")
    return await judge(instruction=instruction, iteration=iteration,
                       max_iterations=max_iterations, facts=facts,
                       still_streak=still_streak, advisory=advisory,
                       replay=replay, driver=driver, agent=gen_agent,
                       cwd=cwd)
