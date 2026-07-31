"""Assemble a bounded conversation window for `/btw`.

Pure: events in, text out. No LLM, no disk, no bridge — which is why this
is the piece worth testing hard. Everything downstream of it is one API
call.

Two properties are invariants rather than details:

- **Newest-first.** The window fills backwards from the newest event.
  Truncating from the front would drop the turn that prompted the
  question, and `/btw` would confidently answer a question nobody asked.
- **Honest about what it dropped.** ``Window.header`` states the turns and
  items it left out, and that string goes to the model *and* to the
  reader. A silently shortened transcript reads as a conversation that
  was always this short — the same principle as the ``⚠ damaged
  record(s) skipped`` marker in ``replay_blocks``.
"""
from __future__ import annotations

from dataclasses import dataclass

from aegis.events import (
    AgentPlan, AssistantText, Result, ToolResult, ToolUse, UserMessage,
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

_PLAN_GLYPH = {"completed": "x", "in_progress": ">", "pending": " "}


@dataclass(frozen=True)
class Window:
    """A bounded slice of a conversation, plus what it cost to bound it."""
    text: str
    header: str
    turns_included: int
    turns_total: int
    truncated: int          # items clipped to ITEM_CHARS
    bound_by: str           # "turns" | "budget" | "all"

    @property
    def approx_tokens(self) -> int:
        return len(self.text) // 4


def _clip(text: str, limit: int) -> tuple[str, bool]:
    text = text.strip()
    if len(text) <= limit:
        return text, False
    return f"{text[:limit].rstrip()} … [+{len(text) - limit:,} chars]", True


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
        text, clipped = _clip(ev.text or "", item_chars)
        label = "result[error]" if ev.is_error else "result"
        return f"{label}: {text}", clipped
    if isinstance(ev, AgentPlan):
        if not ev.entries:
            return None
        rows = "; ".join(
            f"[{_PLAN_GLYPH.get(e.status, ' ')}] {e.content}"
            for e in ev.entries)
        return f"plan: {rows}", False
    return None


def _header(included: int, total: int, truncated: int) -> str:
    if included >= total:
        head = f"all {total} turn" + ("s" if total != 1 else "")
    else:
        head = f"last {included} of {total} turns"
    if truncated:
        head += (f" · {truncated} item"
                 f"{'s' if truncated != 1 else ''} truncated")
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


def assemble(replay, *, max_turns: int = MAX_TURNS,
             budget_tokens: int = BUDGET_TOKENS,
             item_chars: int = ITEM_CHARS) -> Window:
    """Fill a window backwards from the newest event until a bound trips.

    ``replay`` is an ``EventReplay``. The turn boundary is the ``Result``
    event, which terminates a turn; a trailing run of events with no
    ``Result`` after it is a turn still in flight, and it is the most
    relevant thing in the window, so it is always included first.
    """
    events = coalesce_chunks(replay.events)

    lines: list[str] = []
    used = truncated = crossed = 0
    bound = "all"
    saw_result = trailing = False

    for ev in reversed(events):
        if isinstance(ev, Result):
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
        cost = len(line) // 4 + 1
        if used + cost > budget_tokens:
            if lines:
                bound = "budget"
                break
            # A single item larger than the whole budget must still
            # produce something rather than an empty window.
            line, clipped = _clip(line, budget_tokens * 4)
            cost = len(line) // 4
            bound = "budget"
        used += cost
        truncated += clipped
        lines.append(line)

    lines.reverse()

    total = _count_turns(events, item_chars)
    included = min(crossed + (1 if trailing else 0), total)

    return Window(text="\n".join(lines),
                  header=_header(included, total, truncated),
                  turns_included=included, turns_total=total,
                  truncated=truncated, bound_by=bound)
