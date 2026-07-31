"""`@peer` — asking an idle agent, from where you're standing.

You have ten tabs open and eight are idle, each holding a warm context
that cost real money to build and is currently returning nothing.
``@peer`` extracts an answer from one of them without switching tabs and
without retyping the context.

It sits in the hole between the two things that shipped on 2026-07-31:

    aegis_handoff   no context, you retype it        free      live peer
    @peer           a bounded slice of where you are  one turn  idle peer
    /fork           the entire conversation           ~$1       new agent

**Idle-only is the domain, not a shortcut.** The economic case is
extracting value from a context that is currently producing nothing; a
busy peer inverts it — it is already producing value, and cutting in
costs the very thing the ask was for. The guard therefore reads the
*target* and never the source: asking an idle peer while your own tab is
mid-turn is the point, not an edge case.

Spec: ``docs/superpowers/specs/2026-07-31-aegis-at-mention-peer-ask-design.md``
"""
from __future__ import annotations

from dataclasses import dataclass

# How long the source pane waits. On overrun the peer's turn keeps going
# and lands in its own transcript, so nothing is lost — the operator is
# told to go read it there rather than being left with a hung pane.
PEER_ASK_TIMEOUT_S = 300.0


class PeerBusy(Exception):
    """The target was idle at the check and busy at the delivery.

    Raised only from the send path, where the delivery receipt is the last
    honest word on whether the peer took the turn now or buffered it.
    """

    def __init__(self, handle: str) -> None:
        super().__init__(handle)
        self.handle = handle


@dataclass(frozen=True)
class PeerAnswer:
    """One peer's answer, and what it cost to get it.

    Plain fields only: the web seam ships ``CommandResult.effect`` straight
    out as JSON, so this crosses that boundary as an ``asdict`` — the exact
    shape ``/btw`` had to adopt after a dataclass broke it on the web
    client and nowhere else.
    """
    answer: str = ""
    target: str = ""
    header: str = ""            # the teaser window's own honest header
    model: str = ""
    duration_ms: int = 0
    cost_usd: float = 0.0
    ok: bool = False
    error: str = ""

    @property
    def footer(self) -> str:
        """Who answered, and the price — shown because this is a paid turn."""
        bits = [b for b in (
            self.target,
            self.model,
            f"{self.duration_ms / 1000:.1f}s" if self.duration_ms else "",
            f"${self.cost_usd:.4f}" if self.cost_usd else "",
            self.header,
        ) if b]
        return " · ".join(bits)


def refusal(*, from_handle: str, target: str, session, ready: bool) -> str | None:
    """Why this ask must not be sent, or None if it may be.

    Every refusal names the alternative. A refused ask is never delivered,
    so it cannot cost the peer a turn.
    """
    if not target:
        return "a peer ask needs a target — try @<handle> <question>"
    if target == from_handle:
        return f"{target} cannot ask itself — that is what /btw is for"
    if session is None:
        return f"unknown session: {target}"
    if not ready:
        return (f"{target} is mid-turn. Wait for it to finish, or /enqueue "
                f"the task instead.")
    return None


def compose(*, source: str, slug: str, prompt: str) -> str:
    """The body the peer receives.

    Provenance of **place, not author**. Tagged as though the source agent
    were asking, the peer reads it as peer-to-peer delegation and skews
    autonomous — it goes and *does* things. The truthful framing is that
    the operator asked, while standing somewhere else.
    """
    return (
        f"The operator typed this from inside another conversation — tab "
        f"`{source}` ({slug}) — and it probably refers to what is "
        f"happening there.\n\n"
        f"Answer it. Do not start long work: if this needs real work "
        f"rather than an answer, say so and stop, and the operator will "
        f"delegate it properly.\n\n"
        f"The operator's question: {prompt}"
    )


async def send_and_await(session, *, prompt: str, sender: str,
                         timeout: float | None = None,
                         require_landed: bool = False,
                         sink: list | None = None) -> str:
    """Deliver to a live session and await its next complete reply.

    Arm before delivering: ``deliver`` starts the turn synchronously when
    the target is idle, so a caller that armed afterwards would miss it.
    """
    import asyncio

    from aegis.queue.schema import InboxMessage, now_iso

    fut = session.capture_next_reply(sink=sink)
    msg = InboxMessage(sender=sender, timestamp=now_iso(), body=prompt)
    receipt = await session.deliver(msg)
    if require_landed and receipt.disposition != "landed":
        # The idle check and the delivery are two moments; the target can
        # go busy in between. A queued message resolves at the peer's next
        # turn boundary, so waiting on it does not fail — it hangs for the
        # whole timeout. Withdraw and refuse instead.
        fut.cancel()
        session.cancel_pending(msg)
        raise PeerBusy(session.handle)
    if timeout is None:
        return await fut
    return await asyncio.wait_for(fut, timeout)


async def ask(*, from_handle: str, target: str, source_slug: str,
              target_session, prompt: str) -> PeerAnswer:
    """The half both ``AppBridge`` implementations share.

    Best-effort by contract, exactly as ``side_note`` is: every failure
    comes back as a ``PeerAnswer`` with ``ok=False`` and a reason, because
    a peer ask must never be able to disturb the conversation it sits
    beside.
    """
    import asyncio
    import time

    from aegis.queue.schema import sender_operator_at
    from aegis.tui.state import AgentState

    ready = (target_session is not None
             and target_session.state is not AgentState.working)
    why = refusal(from_handle=from_handle, target=target,
                  session=target_session, ready=ready)
    if why:
        return PeerAnswer(target=target, error=why)

    body = compose(source=from_handle, slug=source_slug or "unknown",
                   prompt=prompt)
    sink: list = []
    t0 = time.monotonic()
    try:
        text = await send_and_await(
            target_session, prompt=body,
            sender=sender_operator_at(from_handle),
            timeout=PEER_ASK_TIMEOUT_S, require_landed=True, sink=sink)
    except PeerBusy:
        return PeerAnswer(
            target=target,
            error=f"{target} went mid-turn before the ask landed. Wait for "
                  f"it to finish, or /enqueue the task instead.")
    except asyncio.TimeoutError:
        return PeerAnswer(
            target=target,
            error=f"{target} did not answer within {PEER_ASK_TIMEOUT_S:.0f}s "
                  f"— its turn is still running, so go read its tab")
    except Exception as e:                                    # noqa: BLE001
        return PeerAnswer(target=target, error=f"{type(e).__name__}: {e}")

    res = sink[0] if sink else None
    return PeerAnswer(
        answer=text, target=target, ok=True,
        model=getattr(getattr(target_session, "agent", None), "model", "") or "",
        duration_ms=int((time.monotonic() - t0) * 1000),
        cost_usd=float(getattr(res, "cost_usd", 0.0) or 0.0))
