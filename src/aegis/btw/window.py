"""Assemble a bounded conversation window for `/btw`.

Pure: events in, text out. No LLM, no disk, no bridge — which is why this
is the piece worth testing hard. Everything downstream of it is one API
call.

Three properties are invariants rather than details:

- **Newest-first.** The window fills backwards from the newest event.
  Truncating from the front would drop the turn that prompted the
  question, and `/btw` would confidently answer a question nobody asked.
- **Prose keeps a seat.** A fixed share of the budget (``PROSE_SHARE``) is
  reserved for what was *said*, because newest-first alone spends the whole
  window on the last dozen tool calls and never reaches the ``user:`` line
  that opened the turn. This reorders which items get in, never the order
  they are read in — a window that reads out of order invents a causality.
- **Honest about what it dropped.** ``Window.header`` states the turns and
  items it left out, and that string goes to the model *and* to the
  reader. A silently shortened transcript reads as a conversation that
  was always this short — the same principle as the ``⚠ damaged
  record(s) skipped`` marker in ``replay_blocks``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from aegis.events import (
    AgentPlan,
    AssistantText,
    Result,
    ToolResult,
    ToolUse,
    UserMessage,
)
from aegis.render import coalesce_chunks

# Ten turns is a bound on how far back we scan, not a policy knob: measured
# over three real logs (176 / 10 / 12 turns), ten turns weighs 15k-48k
# tokens, so the budget binds first every time. Kept as a cheap guard for
# the degenerate case of many tiny turns.
MAX_TURNS = 10

# ~32k tokens of window. Measured: 10k admitted only 4-7 turns of real
# conversation, which is thin for a question whose answer is eight turns
# back.
BUDGET_TOKENS = 32_000

# Per-item cap for tool calls and their results. Applies to ToolUse
# summaries too, not just results: ``_summarize_tool`` falls through to the
# first string value for any tool outside ``_TOOL_SUMMARY_KEY``, so a
# ``Task`` dispatch contributes its entire subagent prompt. Measured at
# 98,827 chars of tool-use summary in one ten-turn window - more than the
# tool results and assistant text combined.
ITEM_CHARS = 500

# Share of the budget the agent's own prose may claim, so that tool calls keep
# the rest. Filling purely backwards spends the whole window on the last dozen
# tool calls, because that is what the newest events in an agent transcript
# always are, and the `user:` line that opened the turn is never reached.
# Measured over 71 real `/spawn` preambles: 30 of them (42%) carried not one
# `user:` line, and raising the budget did not change the ratio, only the bill
# — at 24k it bought 1 user line against 261 tool lines.
PROSE_SHARE = 0.6

# Admission order, each tier with the ceiling it may spend up to. Read it as a
# priority list: the operator's own words first and against the whole budget,
# then the agent's prose up to its share, then tool calls, then the agent's
# prose again for whatever the tool calls did not want.
#
# The operator's line gets the full budget rather than a share because a share
# is a cap, and a cap loses the one item that carries the referent. Replaying
# 601 real September windows caught it twice over: a 938-token closing answer
# filled a 1,200-token prose share and pushed out the 293-token `user:` line
# behind it with 769 tokens still unspent, and elsewhere a single 1,396-token
# `user:` message did not fit the share at all. Seven windows lost their
# operator line to those two shapes.
#
# The final tier is the mop-up: without it a prose-heavy, tool-light window
# would cap the agent's prose at its share and then leave the rest of the
# budget unspent.
_TIERS: tuple[tuple[tuple[str, ...], float], ...] = (
    (("user:",), 1.0),
    (("assistant:", "plan:"), PROSE_SHARE),
    (("tool:", "tools:", "result"), 1.0),
    (("assistant:", "plan:"), 1.0),
)

_PLAN_GLYPH = {"completed": "x", "in_progress": ">", "pending": " "}


@dataclass(frozen=True)
class Window:
    """A bounded slice of a conversation, plus what it cost to bound it."""

    text: str
    header: str
    turns_included: int
    turns_total: int
    truncated: int  # items clipped to ITEM_CHARS
    bound_by: str  # "turns" | "budget" | "all"

    @property
    def approx_tokens(self) -> int:
        return len(self.text) // 4


def _clip(text: str, limit: int) -> tuple[str, bool]:
    text = text.strip()
    if len(text) <= limit:
        return text, False
    return f"{text[:limit].rstrip()} … [+{len(text) - limit:,} chars]", True


# A tool result that is an inline image rather than text. Claude returns these
# from Read on a PNG as a content block carrying the whole file base64-encoded.
_IMAGE_PAYLOAD = re.compile(r'"type"\s*:\s*"image"')


def _is_image_payload(text: str) -> bool:
    """Is this result an encoded image rather than something a reader can read?

    Worth a branch of its own because ``_clip`` cannot help here. A 200-char
    prefix of base64 is not a 200-char summary — it carries no information at
    all, and it still spends a full item slot. Measured on a real `/spawn`
    tail: three ``Read(*.png)`` results at 190KB each took the whole window
    and pushed the operator's own words out of it.
    """
    return '"base64"' in text and bool(_IMAGE_PAYLOAD.search(text))


def _render(ev, item_chars: int) -> tuple[str, bool] | None:
    """One event as one window line, plus whether it was clipped.

    Returns None for anything that does not belong in the window:
    AssistantThinking (claude redacts the text, so it is the worst
    tokens-per-insight in the log), and the SystemInit / ContextUpdate /
    SessionMeta / Unknown noise.
    """
    if isinstance(ev, UserMessage):
        text = ev.text.strip()
        return (f"user: {text}", False) if text else None
    if isinstance(ev, AssistantText):
        text = ev.text.strip()
        return (f"assistant: {text}", False) if text else None
    if isinstance(ev, ToolUse):
        summary, clipped = _clip(ev.summary or "", item_chars)
        return f"tool: {ev.name}({summary})", clipped
    if isinstance(ev, ToolResult):
        label = "result[error]" if ev.is_error else "result"
        raw = ev.text or ""
        if _is_image_payload(raw):
            # Named, not dropped: a missing line reads as a call that returned
            # nothing, and the agent does need to know an image came back.
            return f"{label}: [image, {len(raw):,} chars]", True
        text, clipped = _clip(raw, item_chars)
        return f"{label}: {text}", clipped
    if isinstance(ev, AgentPlan):
        if not ev.entries:
            return None
        rows = "; ".join(
            f"[{_PLAN_GLYPH.get(e.status, ' ')}] {e.content}" for e in ev.entries
        )
        return f"plan: {rows}", False
    return None


def _header(included: int, total: int, truncated: int) -> str:
    if included >= total:
        head = f"all {total} turn" + ("s" if total != 1 else "")
    else:
        head = f"last {included} of {total} turns"
    if truncated:
        head += f" · {truncated} item{'s' if truncated != 1 else ''} truncated"
    return head


def _count_turns(events, item_chars: int) -> int:
    """Complete turns (``Result`` events) plus one for a turn in flight."""
    total = sum(1 for e in events if isinstance(e, Result))
    for e in reversed(events):
        if isinstance(e, Result):
            break
        if _render(e, item_chars) is not None:
            total += 1
            break
    return total


def assemble(
    replay,
    *,
    max_turns: int = MAX_TURNS,
    budget_tokens: int = BUDGET_TOKENS,
    item_chars: int = ITEM_CHARS,
    tools: bool = True,
) -> Window:
    """Fill a window backwards from the newest event until a bound trips.

    ``replay`` is an ``EventReplay`` or a plain sequence of events (a
    mid-turn caller holds the live list, not a replayed log). The turn boundary is the ``Result``
    event, which terminates a turn; a trailing run of events with no
    ``Result`` after it is a turn still in flight, and it is the most
    relevant thing in the window, so it is always included first.

    Two passes, because one cannot do it. The first renders every candidate in
    the turn window and spends nothing; the second admits prose up to
    ``PROSE_SHARE`` of the budget and then tool calls into what is left. A
    single backward pass that stopped at the first over-budget item gave the
    whole window to the newest tool calls, which is the least informative part
    of a transcript and the reason a spawned agent arrived unable to resolve
    "verify this test".
    """
    events = coalesce_chunks(getattr(replay, "events", replay))

    # Pass 1: the candidates, newest first, bounded by turns only. Rendering
    # them all before spending any budget is what lets pass 2 choose; the walk
    # is the same length the old single pass took on a window that fit.
    candidates: list[tuple[int, str, bool]] = []  # pos, line, clipped
    crossed = 0
    bound = "all"
    saw_result = trailing = False

    # tools=False: a run of consecutive tool calls becomes one line, and
    # their results are dropped. Done here rather than after admission, so
    # the room the calls no longer take goes to the conversation.
    run: list[str] = []  # tool names, newest first
    run_pos = 0

    def flush_run() -> None:
        if not run:
            return
        counts: dict[str, int] = {}
        for name in reversed(run):
            counts[name] = counts.get(name, 0) + 1
        names = ", ".join(f"{n}×{c}" if c > 1 else n for n, c in counts.items())
        candidates.append((run_pos, f"tools: {names}", False))
        run.clear()

    for pos in range(len(events) - 1, -1, -1):
        ev = events[pos]
        if not tools and isinstance(ev, (ToolUse, ToolResult)):
            if not saw_result:
                trailing = True
            if isinstance(ev, ToolUse):
                run.append(ev.name)
                run_pos = pos
            continue
        if isinstance(ev, Result):
            flush_run()
            saw_result = True
            if crossed >= max_turns:
                bound = "turns"
                break
            crossed += 1
            continue
        rendered = _render(ev, item_chars)
        if rendered is None:
            continue
        line, clipped = rendered
        if not saw_result:
            trailing = True
        flush_run()
        candidates.append((pos, line, clipped))
    flush_run()

    # Pass 2: admit by tier, each tier newest-first and bounded by its own
    # ceiling. This reorders which items get a seat and never the order they
    # are read in.
    taken: dict[int, tuple[str, bool]] = {}
    spent = 0
    rejected = False
    for prefixes, share in _TIERS:
        ceiling = min(int(budget_tokens * share), budget_tokens)
        for pos, line, clipped in candidates:
            if pos in taken or not line.startswith(prefixes):
                continue
            cost = len(line) // 4 + 1
            if spent + cost > ceiling:
                rejected = True
                continue
            spent += cost
            taken[pos] = (line, clipped)
    if rejected:
        bound = "budget"

    if not taken and candidates:
        # A single item larger than the whole budget must still produce
        # something rather than an empty window.
        pos, line, _ = candidates[0]
        taken[pos] = _clip(line, budget_tokens * 4)
        bound = "budget"

    lines = [taken[pos][0] for pos in sorted(taken)]
    truncated = sum(1 for pos in taken if taken[pos][1])

    total = _count_turns(events, item_chars)
    included = min(crossed + (1 if trailing else 0), total)

    return Window(
        text="\n".join(lines),
        header=_header(included, total, truncated),
        turns_included=included,
        turns_total=total,
        truncated=truncated,
        bound_by=bound,
    )
