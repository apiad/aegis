"""A recap: two sentences for a person who comes back to a tab after a while.

The browser asks when it opens a tab; the server decides whether the unread
stretch is long enough (``needed``), builds the window the model reads
(``window``), and runs one cheap harness-specific generation call (``argv``,
``parse``) through the one-shot path it shares with /btw (oneshot.py, where the
flags are explained).
The recap is aegis talking to the person; nothing here reaches the agent.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from . import oneshot

MIN_UNREAD = 2
LONG_WORDS = 300
AWAY_S = 1800
WINDOW_CHARS = 12_000
ASK_CHARS = 600
TIMEOUT_S = 60


class RecapOut(BaseModel):
    context: str = Field(
        description="One sentence: what the session was working on, at the level of the goal."
    )
    ask: str = Field(
        description="One sentence: what it needs from the person now. Empty if nothing."
    )


SYSTEM = (
    "You tell a person, returning to a coding agent's session after a while, where "
    "it stands. Speak at the level of intent and outcome: the problem being solved, "
    "what got solved or decided, and what it needs from them. Never list files, "
    "commits, hashes, task ids, ports, test counts or tool calls, and avoid numbers "
    "unless the number is the point. The AGENT REPORT and PLAN blocks are the "
    "agent's own account: trust them over your reading of the transcript. Prefer "
    "what actually happened over what the agent said it would do. LANGUAGE: write "
    "both fields in the language of the person's own messages (the lines marked "
    "`user:`), even when the agent answers in another language. `context` is one "
    "sentence of at most 25 words. `ask` restates only a question or request the "
    "agent actually put to the person, in the transcript or the AGENT REPORT; never "
    "invent one. When the AGENT REPORT is done or review and the last agent message "
    "asks nothing, `ask` is empty. No preamble, no praise."
)


def needed(
    entries: list[dict], unread: set[str], last_read_at: float | None, now: float
) -> bool:
    if not unread:
        return False
    if len(unread) >= MIN_UNREAD:
        return True
    if last_read_at is not None and now - last_read_at > AWAY_S:
        return True
    return any(
        e["id"] in unread and len((e.get("md") or "").split()) > LONG_WORDS
        for e in entries
        if e["kind"] == "prose"
    )


def _line(e: dict) -> str | None:
    md = (e.get("md") or "").strip()
    if e["kind"] == "user":
        return f"user: {md}"
    if e["kind"] == "prose":
        return f"agent: {md}"
    if e["kind"] == "tool":
        result = (e.get("detail") or {}).get("result", "")
        return f"tool: {e.get('title', '')} {e.get('summary', '')} -> {result}".strip()
    if e["kind"] == "inbox":
        return f"message to the agent: {e.get('title', '')}"
    return None


def window(entries: list[dict], unread: set[str], standing: dict) -> str:
    users = [k for k, e in enumerate(entries) if e["kind"] == "user"]
    firsts = [
        k for k, e in enumerate(entries) if e["kind"] == "prose" and e["id"] in unread
    ]
    starts = [
        k
        for k in (users[-1] if users else None, firsts[0] if firsts else None)
        if k is not None
    ]
    start = min(starts) if starts else 0
    lines = [x for e in entries[start:] if (x := _line(e))]
    body = "\n".join(lines)[-WINDOW_CHARS:]
    # The cut drops the oldest lines, which is where the person's message sits,
    # and SYSTEM takes the recap's language from it: pin it back, shortened.
    asks = [k for k, x in enumerate(lines) if x.startswith("user: ")]
    if asks and len("\n".join(lines[asks[-1] :])) > WINDOW_CHARS:
        body = f"{lines[asks[-1]][:ASK_CHARS]}\n[...]\n{body}"
    parts = [f"--- transcript ---\n{body}\n--- end ---"]
    report = standing.get("report")
    if report:
        parts.append(f"AGENT REPORT ({report['attention']}): {report['line']}")
    if standing.get("turn_error"):
        parts.append(f"ERROR: {standing['turn_error']}")
    plan = standing.get("plan") or []
    if plan:
        parts.append(
            "PLAN:\n" + "\n".join(f"- [{i['state']}] {i['text']}" for i in plan)
        )
    return "\n\n".join(parts)


def argv(binary: str, model: str, *, harness: str = "claude-code") -> list[str]:
    return oneshot.argv(binary, model, RecapOut, SYSTEM, harness=harness)


def parse(stdout: str) -> tuple[RecapOut | None, float, int]:
    """The envelope's structured answer, cost and time. Never raises."""
    return oneshot.parse(stdout, RecapOut)


def load_recap(config_root: Path) -> dict | None:
    """``recap: {agent: <name>}``: None when absent, else the agent or why not.
    Nothing defaults (agents.py)."""
    return oneshot.load_generator(config_root, "recap")
