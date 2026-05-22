"""Inbox notifications for terminal command-finish events."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

from aegis.terminal.manager import CommandRecord


STDOUT_TAIL_LINES = 8
STDERR_TAIL_LINES = 4
LINE_TRUNC = 200


@dataclass
class InboxMessage:
    sender: str
    body: str


class InboxRouterLike(Protocol):
    async def deliver(self, handle: str, message: InboxMessage) -> None: ...


def _trunc(line: str, n: int) -> str:
    return line if len(line) <= n else line[: n - 1] + "…"


def _tail(text: str, n: int) -> str:
    lines = text.splitlines()[-n:]
    return "\n".join(_trunc(ln, LINE_TRUNC) for ln in lines)


def build_inbox_message(name: str, rec: CommandRecord) -> InboxMessage:
    exit_part = f"exit {rec.exit}" if rec.exit is not None else "exit ?"
    duration_part = f"{rec.duration_s:.2f}s" if rec.duration_s else "?"
    body = (
        f"$ {rec.cmd}  · run by {rec.writer}\n"
        f"{exit_part} · {duration_part}\n"
        f"──\n"
        f"{_tail(rec.stdout, STDOUT_TAIL_LINES)}"
    )
    if rec.stderr:
        body += f"\n── stderr ──\n{_tail(rec.stderr, STDERR_TAIL_LINES)}"
    return InboxMessage(sender=f"term:{name}", body=body)


Notifier = Callable[[str, CommandRecord, list[str]], Awaitable[None]]


def make_terminal_notifier(router: InboxRouterLike) -> Notifier:
    async def notifier(name: str, rec: CommandRecord, subscribers: list[str]) -> None:
        msg = build_inbox_message(name, rec)
        for handle in subscribers:
            if handle == rec.writer:
                continue
            await router.deliver(handle, msg)
    return notifier
