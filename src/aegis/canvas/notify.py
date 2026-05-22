"""Canvas → InboxMessage notification builder.

Translates a ``WriteResult`` into an ``InboxMessage`` per subscriber
and delivers it through ``InboxRouter``. Suppresses self-notifications
(writer doesn't get their own writes echoed back).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from aegis.canvas.manager import WriteResult
from aegis.queue.schema import InboxMessage

if TYPE_CHECKING:
    from aegis.canvas.manager import _CanvasState  # noqa: F401
    from aegis.queue.inbox import InboxRouter

# How many lines of the new content to show in the inbox preview.
PREVIEW_LINES = 6


def _format_byline(result: WriteResult) -> str:
    if result.op == "append":
        return (f"appended by agent:{result.writer} "
                f"(+{result.added} lines)")
    return (f"written by agent:{result.writer} "
            f"(+{result.added} / -{result.removed} lines)")


def _format_preview(result: WriteResult) -> str:
    # On append, show only the appended text; on write, show the new body.
    body = (result.appended_text if result.op == "append"
            else result.new_body)
    if body is None:
        body = ""
    lines = body.splitlines()
    if not lines:
        return "(empty)"
    head = lines[:PREVIEW_LINES]
    out = "\n".join(head)
    if len(lines) > PREVIEW_LINES:
        out += f"\n… ({len(lines) - PREVIEW_LINES} more lines)"
    return out


def build_inbox_body(result: WriteResult) -> str:
    """Render the body that appears below the substrate header.

    Final delivered message will be:
        > from canvas:<canvas> · section "<section>" · <ts>
        <byline>
        ──
        <preview>
    """
    sender_line = (f"section \"{result.section}\" · {result.timestamp}")
    byline = _format_byline(result)
    preview = _format_preview(result)
    return f"{sender_line}\n{byline}\n──\n{preview}"


def build_inbox_message(result: WriteResult) -> InboxMessage:
    """Wrap a WriteResult as an InboxMessage with sender canvas:<name>."""
    return InboxMessage(
        sender=f"canvas:{result.canvas}",
        timestamp=result.timestamp,
        body=build_inbox_body(result),
    )


async def dispatch_notifications(result: WriteResult,
                                 subscribers: list[str],
                                 inbox_router: "InboxRouter") -> list[str]:
    """Deliver one InboxMessage per subscriber, suppressing the writer.

    Returns the list of handles that actually received a message.
    """
    writer_handle = result.writer
    msg = build_inbox_message(result)
    delivered: list[str] = []
    for handle in subscribers:
        if handle == writer_handle:
            continue
        await inbox_router.deliver(handle, msg)
        delivered.append(handle)
    return delivered


def make_canvas_notifier(inbox_router: "InboxRouter"):
    """Return a Notifier suitable for CanvasManager(notifier=...).

    The closure captures the inbox_router so the manager doesn't have to
    know about routing.
    """
    async def _notifier(result: WriteResult, state) -> None:
        subs = list(state.subscribers.keys())
        # Apply section filter the same way subscribers_for_section does,
        # without needing a reference back to the manager.
        targets: list[str] = []
        for h in subs:
            flt = state.subscribers[h]
            if flt is None or result.section in flt:
                targets.append(h)
        await dispatch_notifications(result, targets, inbox_router)
    return _notifier
