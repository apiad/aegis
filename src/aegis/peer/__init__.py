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

# The teaser's job is to *place* the peer, not to let it answer from the
# window alone — that is what ``aegis_read_peer`` is for. `/btw` runs at
# 32k and measured the window as most of its bill; this is a small
# fraction of that, and it costs a log read rather than a model call.
# A number to measure once peers are actually pulling, not to guess at
# forever: if the pull rate is high, this is too small; if peers answer
# blind, it is too big.
TEASER_BUDGET_TOKENS = 2_000
TEASER_MAX_TURNS = 3
TEASER_ITEM_CHARS = 200

# What ``aegis_read_peer`` returns when the teaser was not enough. Wider
# than the teaser by design — that gap is the entire push-vs-pull split.
READ_BUDGET_TOKENS = 24_000
READ_ITEM_CHARS = 500


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


async def teaser(state_dir, log_id):
    """A free slice of where the operator is standing, or None.

    Free is the point: a log read and an ``assemble()`` call, never a
    model call. The design bets on *pull* — send a cheap pointer and let
    the peer read more if it needs to — and that bet only pays while the
    pointer costs nothing. Summarising here would tax every ask, including
    the many whose question was self-contained anyway.

    Returns None rather than raising on any failure: a missing transcript
    must cost the operator the teaser, never the ask.
    """
    import asyncio

    if not state_dir or not log_id:
        return None
    try:
        from aegis.btw.window import assemble
        from aegis.state import session_log
        replay = await asyncio.to_thread(
            session_log.replay_events, state_dir, log_id)
        return assemble(replay, max_turns=TEASER_MAX_TURNS,
                        budget_tokens=TEASER_BUDGET_TOKENS,
                        item_chars=TEASER_ITEM_CHARS)
    except Exception:                                         # noqa: BLE001
        return None


async def read_window(state_dir, log_id, turns: int = 12) -> dict:
    """Window a peer's transcript for ``aegis_read_peer``.

    Unlocks nothing: the logs are plain JSONL inside the project root and
    every agent already has Read and Bash. What this adds is *addressing*
    — the log id carries the session's **birth** handle and is never
    renamed, so current-handle → file is not derivable by an agent that
    only knows who it is talking to.
    """
    import asyncio

    if not state_dir or not log_id:
        return {"ok": False, "text": "", "header": "",
                "error": "that session has no persisted transcript to read"}
    try:
        from aegis.btw.window import assemble
        from aegis.state import session_log
        replay = await asyncio.to_thread(
            session_log.replay_events, state_dir, log_id)
        w = assemble(replay, max_turns=turns,
                     budget_tokens=READ_BUDGET_TOKENS,
                     item_chars=READ_ITEM_CHARS)
    except Exception as e:                                    # noqa: BLE001
        return {"ok": False, "text": "", "header": "",
                "error": f"could not read the transcript: {e}"}
    return {"ok": True, "text": w.text, "header": w.header, "error": ""}


async def cc_into(source_session, answer: PeerAnswer) -> None:
    """Deliver a peer's answer into the source conversation as a real turn.

    Opt-in, and decided by the operator at *send* time rather than by the
    peer at reply time — the peer cannot judge relevance to a conversation
    it has, at best, a 3-turn window of, and being agreeable it would
    guess yes, which is the expensive default arriving through the back
    door.

    A courtesy on top of an answer the operator already has, so it must
    never turn a successful ask into a failed one: failures here are
    swallowed, and a refusal is never cc'd at all.

    **`except Exception` below is deliberate and must not be widened.**
    ``CancelledError`` derives from ``BaseException`` (since 3.8), so the
    handler does not catch it — and that is the only reason pressing ESC
    on a deferred `@peer` also cancels the cc. Widen it to
    ``except BaseException`` or a bare ``except:`` and cancellation is
    silently swallowed: the peer's turn finishes regardless, and its
    answer lands in a conversation the operator walked away from minutes
    earlier. It reads like an over-narrow handler somebody should tidy;
    it is the cancellation contract. Pinned by
    ``test_cc_does_not_swallow_cancellation``.
    """
    if not answer.ok or source_session is None:
        return
    try:
        from aegis.queue.schema import InboxMessage, now_iso, sender_agent
        await source_session.deliver(InboxMessage(
            sender=sender_agent(answer.target), timestamp=now_iso(),
            body=f"The operator asked @{answer.target} from this "
                 f"conversation, and it answered:\n\n{answer.answer}"))
    except Exception:                                         # noqa: BLE001
        pass


def compose(*, source: str, slug: str, prompt: str, window=None) -> str:
    """The body the peer receives.

    Two things this wording has to get right.

    **Provenance of place, not author.** Tagged as though the source agent
    were asking, the peer reads it as peer-to-peer delegation and skews
    autonomous — it goes and *does* things. The truthful framing is that
    the operator asked, while standing somewhere else.

    **Default to pull.** "If you need to, call ``aegis_read_peer``" biases
    toward not calling, and the failure mode is not laziness — it is that
    the model cannot detect the gap. "Look at this and tell me if it's
    right" is complete-seeming English; nothing in it signals a missing
    referent, and noticing an absence is what context windows are worst
    at. So the burden sits on answering *without* reading, and the
    window's honest header ("6 of 143 events") is included precisely to
    turn an undetectable absence into a legible one.
    """
    if window is not None and window.text:
        slice_ = (f"Below is the recent tail of that conversation — "
                  f"{window.header}.\n\n"
                  f"--- tail of {source} ---\n{window.text}\n--- end ---\n\n")
        pull = (f'Read the fuller conversation with '
                f'aegis_read_peer("{source}") before answering, unless the '
                f'question is plainly self-contained.\n\n')
    else:
        slice_ = (f"Its transcript could not be read, so you are seeing "
                  f"none of it.\n\n")
        pull = (f'Read it with aegis_read_peer("{source}") before '
                f'answering, unless the question is plainly '
                f'self-contained.\n\n')
    return (
        f"The operator typed this from inside another conversation — tab "
        f"`{source}` ({slug}) — and it probably refers to what is "
        f"happening there. {slice_}"
        f"{pull}"
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
              target_session, prompt: str,
              state_dir=None, source_log_id: str | None = None,
              source_session=None, cc: bool = False) -> PeerAnswer:
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

    window = await teaser(state_dir, source_log_id)
    body = compose(source=from_handle, slug=source_slug or "unknown",
                   prompt=prompt, window=window)
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
    answer = PeerAnswer(
        answer=text, target=target, ok=True,
        header=getattr(window, "header", "") or "no transcript",
        model=getattr(getattr(target_session, "agent", None), "model", "") or "",
        duration_ms=int((time.monotonic() - t0) * 1000),
        cost_usd=float(getattr(res, "cost_usd", 0.0) or 0.0))
    if cc:
        await cc_into(source_session, answer)
    return answer
