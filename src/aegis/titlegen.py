"""Generate a session title in one shot — no session, no MCP, no tools.

The counterpart to ``aegis.state.titles``, which is pure: this is the half
that talks to a model. It rides the same ``HarnessDriver.generate_detailed``
seam ``/btw`` uses, and picks its billing profile the same way
(``text_generation:`` via ``btw.generation_agent``), so a title never costs
Opus tokens unless the operator never set the knob.

**Best-effort by contract.** Every failure — a driver that raises, a model
that returns prose, an empty result — comes back as ``""``. A title is a
convenience and the conversation it labels is not, so generation must never
be able to disturb a turn.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from aegis.state.titles import sanitize_title


class TitleSuggestion(BaseModel):
    title: str = Field(description="3-8 words, no quotes, no trailing "
                                   "punctuation")


# t3code's, which is well-tuned; reproduced rather than reinvented.
_SYSTEM = (
    "You write concise thread titles for coding conversations. "
    "Summarize the user's request, not restate it verbatim. Keep it short "
    "and specific (3-8 words). Avoid quotes, filler, prefixes, and "
    "trailing punctuation."
)

_FIRST = "The conversation opens with this operator message:"

# The regeneration variant differs meaningfully: it is told what the title
# was, and asked about the thread's *current* state rather than its opening
# request — which is the whole reason to regenerate at all.
_AGAIN = (
    "This conversation is already titled {previous!r}. Summarize what it "
    "is about NOW, not what it opened with, and return something different "
    "from the current title. Here is the recent conversation:"
)


async def suggest_title(*, opening: str, driver, agent, cwd: str,
                        previous: str | None = None) -> str:
    """One call, sanitized. ``""`` on anything at all going wrong.

    ``opening`` is the first operator message on the first-turn path, or a
    window onto the recent transcript when regenerating.
    """
    if not opening or not opening.strip():
        return ""          # nothing to summarize; don't pay for the call
    lead = _AGAIN.format(previous=previous) if previous else _FIRST
    try:
        gen = await driver.generate_detailed(
            agent, cwd, TitleSuggestion,
            _SYSTEM, lead, opening)
    except Exception:                                         # noqa: BLE001
        return ""
    if gen is None or gen.value is None:
        return ""
    return sanitize_title(gen.value.title)


async def title_for(*, opening: str, agent, agents: dict, cwd: str,
                    previous: str | None = None) -> str:
    """``suggest_title`` with the driver and billing profile resolved.

    The half both call sites share. Mirrors ``btw.side_note_for``: pick the
    ``text_generation:`` profile if one is configured, fall back to the
    session's own agent, and decline quietly when that driver cannot do a
    one-shot at all (gemini / opencode / lovelaice today — they return an
    empty ``Generation`` from the base class, so this is a real path).
    """
    from aegis.btw import generation_agent
    from aegis.drivers import get_driver
    gen_agent, _unset = generation_agent(agent, agents)
    try:
        driver = get_driver(gen_agent.harness)
    except KeyError:
        return ""
    if not getattr(driver, "supports_oneshot", False):
        return ""
    return await suggest_title(opening=opening, driver=driver,
                               agent=gen_agent, cwd=cwd, previous=previous)
