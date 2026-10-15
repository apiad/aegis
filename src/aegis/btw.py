"""/btw: a side question about a session, answered beside it.

A person asks something adjacent to the work, often mid-turn. aegis reads the
session's fold, builds a bounded window, and makes one tool-less call on the
agent ``btw:`` names (oneshot.py). Nothing here reaches the agent.

The window is the legacy tree's (legacy/aegis/btw/window.py), ported from stream
events to fold entries. Three rules are invariants:

- **Newest first, by tier.** Filling purely backwards spends the budget on the
  newest tool calls, which is what an agent transcript always ends with: over 71
  real windows, 30 carried no ``user:`` line. So the person's words are admitted
  first against the whole budget, then the agent's prose up to PROSE_SHARE, then
  tool calls, then prose again with what is left.
- **Read in order.** The tiers decide which lines get a seat, never the order
  they are read in: a window read out of order invents a causality.
- **Honest about what it dropped.** The header names the turns and items left
  out, and goes to the model and to the person.

The question goes last in the prompt, after all context: buried mid-prompt, a
side note starts answering something adjacent to the ask.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

# Ten turns bounds the scan; measured on real logs, the budget binds first.
MAX_TURNS = 10
# About 32k tokens: 10k admitted only 4-7 turns of real conversation.
BUDGET_TOKENS = 32_000
# Per-part cap on a tool line: a Task dispatch's prompt once filled a window.
ITEM_CHARS = 500
PROSE_SHARE = 0.6
# How many earlier side notes go into the prompt, so a follow-up works.
EARLIER = 5
# The window is up to about three times the recap's, on a larger model.
TIMEOUT_S = 90

_TIERS: tuple[tuple[tuple[str, ...], float], ...] = (
    (("user:",), 1.0),
    (("assistant:",), PROSE_SHARE),
    (("tool", "message to the agent:"), 1.0),
    (("assistant:",), 1.0),
)


class BtwOut(BaseModel):
    answer: str = Field(
        description="The answer, in Markdown, in the language of the question."
    )
    needs_more: bool = Field(
        default=False,
        description="True when the window does not hold what the answer needs.",
    )


SYSTEM = (
    "You answer a person's side question about a coding agent's session, from a "
    "window onto its transcript. You have no tools and cannot act. Answer briefly "
    "and directly, in the language of the question. The AGENT REPORT and PLAN "
    "blocks are the agent's own account: trust them over your reading of the "
    "transcript. If the window does not contain what you would need, say what is "
    "missing and set needs_more to true; never guess."
)

PREAMBLE = (
    "Below is a window onto a conversation between a person and a coding agent. "
    "It is a slice, not the whole thing: {header}."
)


@dataclass(frozen=True)
class Window:
    text: str
    header: str
    turns_included: int
    turns_total: int
    truncated: int  # items clipped to ITEM_CHARS
    bound_by: str  # "turns" | "budget" | "all"


def _clip(text: str, limit: int) -> tuple[str, bool]:
    text = text.strip()
    if len(text) <= limit:
        return text, False
    return f"{text[:limit].rstrip()} … [+{len(text) - limit:,} chars]", True


def _line(e: dict, item_chars: int) -> tuple[str, bool] | None:
    kind = e["kind"]
    md = (e.get("md") or "").strip()
    if kind == "user":
        return (f"user: {md}", False) if md else None
    if kind == "prose":
        return (f"assistant: {md}", False) if md else None
    if kind == "tool":
        head, c1 = _clip(f"{e.get('title', '')}({e.get('summary', '')})", item_chars)
        res, c2 = _clip(str((e.get("detail") or {}).get("result") or ""), item_chars)
        label = "tool[error]" if e.get("status") == "err" else "tool"
        return f"{label}: {head}" + (f" -> {res}" if res else ""), c1 or c2
    if kind == "inbox":
        return f"message to the agent: {e.get('title', '')}", False
    return None


def _header(included: int, total: int, truncated: int) -> str:
    if included >= total:
        head = f"all {total} turn" + ("s" if total != 1 else "")
    else:
        head = f"last {included} of {total} turns"
    if truncated:
        head += f" · {truncated} item{'s' if truncated != 1 else ''} truncated"
    return head


def window(
    entries: list[dict],
    *,
    max_turns: int = MAX_TURNS,
    budget_tokens: int = BUDGET_TOKENS,
    item_chars: int = ITEM_CHARS,
) -> Window:
    """The newest turns of ``entries`` that fit, read in transcript order.

    A turn starts at each ``user`` entry; lines before the first one are a turn
    of their own. Two passes: the first renders every candidate in the turn
    window and spends nothing, the second admits by tier."""
    users = [k for k, e in enumerate(entries) if e["kind"] == "user"]
    before = entries[: users[0]] if users else entries
    starts = ([0] if any(_line(e, item_chars) for e in before) else []) + users
    total = len(starts)
    bound = "all"
    start = 0
    if len(users) > max_turns:
        start, bound = users[-max_turns], "turns"
    candidates = [
        (k, *r)
        for k in range(len(entries) - 1, start - 1, -1)
        if (r := _line(entries[k], item_chars)) is not None
    ]
    taken: dict[int, tuple[str, bool]] = {}
    spent = 0
    rejected = False
    for prefixes, share in _TIERS:
        ceiling = int(budget_tokens * share)
        for k, line, clipped in candidates:
            if k in taken or not line.startswith(prefixes):
                continue
            cost = len(line) // 4 + 1
            if spent + cost > ceiling:
                rejected = True
                continue
            spent += cost
            taken[k] = (line, clipped)
    if rejected:
        bound = "budget"
    if not taken and candidates:
        # One item larger than the whole budget still gives a window.
        k, line, _ = candidates[0]
        taken[k] = _clip(line, budget_tokens * 4)
        bound = "budget"
    included = (sum(1 for s in starts if s > min(taken)) + 1) if taken else 0
    truncated = sum(1 for _, c in taken.values() if c)
    return Window(
        text="\n".join(taken[k][0] for k in sorted(taken)),
        header=_header(included, total, truncated) if total else "",
        turns_included=included,
        turns_total=total,
        truncated=truncated,
        bound_by=bound,
    )


def earlier(entries: list[dict]) -> list[tuple[str, str]]:
    """The session's answered side notes, oldest first, as (question, answer)."""
    return [
        (e["detail"].get("question", ""), e["detail"].get("answer", ""))
        for e in entries
        if e["kind"] == "btw" and e["status"] == "ok"
    ]


def prompt(
    question: str, w: Window, standing: dict, earlier: list[tuple[str, str]]
) -> str:
    parts = [
        PREAMBLE.format(header=w.header or "no turns yet"),
        f"--- transcript ---\n{w.text}\n--- end ---",
    ]
    report = standing.get("report")
    if report:
        parts.append(f"AGENT REPORT ({report['attention']}): {report['line']}")
    plan = standing.get("plan") or []
    if plan:
        parts.append(
            "PLAN:\n" + "\n".join(f"- [{i['state']}] {i['text']}" for i in plan)
        )
    if earlier:
        parts.append(
            "EARLIER SIDE NOTES:\n"
            + "\n".join(f"Q: {q}\nA: {a}" for q, a in earlier[-EARLIER:])
        )
    parts.append(f"The person's side question: {question}")
    return "\n\n".join(parts)
