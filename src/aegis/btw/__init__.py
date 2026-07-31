"""`/btw` — a side note that doesn't cost a conversation.

You are mid-task and a question surfaces that is adjacent to the work.
``/btw`` answers it inline and disappears: it reads aegis's own transcript
log, assembles a bounded window, and makes one ``generate()`` call. No
session, no tab, no tools, no MCP.

Because it never touches the harness session, it works **mid-turn** — the
property a fork-shaped `/btw` could not have had, since a live claude
session's tail is a ``tool_use`` with no matching ``tool_result``, and a
fork inherits that dangling call.

Spec: ``docs/superpowers/specs/2026-07-31-aegis-btw-side-note-design.md``
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from pydantic import BaseModel

from aegis.btw.window import assemble


class BtwAnswer(BaseModel):
    """What the model is asked for.

    ``needs_more`` is how the capability `/btw` trades away comes back as a
    signal instead of a guess: the model saying "the window did not contain
    this" beats it answering fluently from a window that did not.
    """
    answer: str
    needs_more: bool = False


@dataclass(frozen=True)
class SideNote:
    """One answered side question, and what it cost to answer."""
    answer: str = ""
    needs_more: bool = False
    header: str = ""            # the window's own honest header
    model: str = ""
    duration_ms: int = 0
    cost_usd: float = 0.0
    ok: bool = False
    error: str = ""
    # True when `text_generation:` was unset and this note was billed at
    # the session's own (usually Opus) rate.
    billed_to_session_profile: bool = False

    @property
    def footer(self) -> str:
        """The price, shown because a side note is a paid call."""
        bits = [b for b in (
            self.model,
            f"{self.duration_ms / 1000:.1f}s" if self.duration_ms else "",
            f"${self.cost_usd:.4f}" if self.cost_usd else "",
            self.header,
        ) if b]
        return " · ".join(bits)


def generation_agent(fallback, agents: dict, root=None):
    """(agent to generate with, whether ``text_generation:`` was unset).

    The knob does real work — measured on zion 2026-07-31, the same
    one-shot costs $0.0044 on haiku — so an unset key is reported rather
    than silently billed at the session's own (usually Opus) rate.

    Read at call time rather than threaded through the constructors: a
    side note happens a handful of times a session, and a config read is
    nothing next to the API call it precedes.
    """
    # yaml_loader.load_config, not config.load_config — the latter is a
    # back-compat wrapper returning (agents, default_agent) and has no
    # text_generation on it at all.
    from aegis.config import find_project_root
    from aegis.config.yaml_loader import load_config
    try:
        root = root or find_project_root()
        if root is not None:
            name = getattr(load_config(root), "text_generation", None)
            if name and name in agents:
                return agents[name], False
    except Exception:                                         # noqa: BLE001
        pass          # a broken config must not cost you the side note
    return fallback, True


_PREAMBLE = (
    "Below is a window onto a conversation between an operator and a "
    "coding agent. It is a slice, not the whole thing: {header}. Answer "
    "the operator's side question from this window alone — you have no "
    "tools. If the window does not contain what you would need, say so "
    "and set needs_more to true rather than guessing."
)


async def side_note(prompt: str, *, replay, driver, agent, cwd: str,
                    **window_opts) -> SideNote:
    """Answer ``prompt`` from ``replay``'s tail, in one call.

    Best-effort by contract. Every failure — a driver that raises, a
    payload that will not parse — comes back as a ``SideNote`` with
    ``ok=False`` and a reason, because a side question must never be able
    to disturb the conversation it sits beside.
    """
    window = assemble(replay, **window_opts)
    try:
        gen = await driver.generate_detailed(
            agent, cwd, BtwAnswer,
            _PREAMBLE.format(header=window.header or "no turns yet"),
            f"--- conversation ---\n{window.text}\n--- end ---",
            f"The operator's side question: {prompt}")
    except Exception as e:                                    # noqa: BLE001
        return SideNote(header=window.header,
                        error=f"{type(e).__name__}: {e}")
    if gen.value is None:
        return SideNote(header=window.header, model=gen.model,
                        duration_ms=gen.duration_ms, cost_usd=gen.cost_usd,
                        error="the model returned nothing usable")
    return SideNote(answer=gen.value.answer, needs_more=gen.value.needs_more,
                    header=window.header, model=gen.model,
                    duration_ms=gen.duration_ms, cost_usd=gen.cost_usd,
                    ok=True)


async def side_note_for(prompt: str, *, state_dir, log_id: str, agent,
                        agents: dict, cwd: str) -> SideNote:
    """Resolve a live session's transcript into an answered side note.

    The half both AppBridge implementations share: pick the billing
    profile, read the log off the event loop, run the one call. Reading a
    24MB transcript takes 0.65s warm — small next to the API call, but far
    too much to spend on the UI thread.
    """
    import asyncio

    from aegis.drivers import get_driver
    from aegis.state.session_log import replay_events

    gen_agent, unset = generation_agent(agent, agents)
    try:
        driver = get_driver(gen_agent.harness)
    except KeyError:
        return SideNote(error=f"unknown harness: {gen_agent.harness!r}")
    if not driver.supports_oneshot:
        return SideNote(
            error=f"the {gen_agent.harness} driver cannot do one-shot "
                  f"generation — point `text_generation:` at one that can")
    try:
        replay = await asyncio.to_thread(replay_events, state_dir, log_id)
    except Exception as e:                                    # noqa: BLE001
        return SideNote(error=f"could not read the transcript: {e}")
    note = await side_note(prompt, replay=replay, driver=driver,
                           agent=gen_agent, cwd=cwd)
    return replace(note, billed_to_session_profile=unset)
