"""Count a token-bench run's transcripts and price them cache-neutral.

Pure: reads the ``projects/**/*.jsonl`` transcripts Claude Code wrote under a
run's private config directory, the main session, its ``subagents/``
sidechains and, in the aegis arm, every session and queue worker on the App.

One API request can span several transcript lines that share a ``requestId``
(text, then each tool call); it counts once, with every tool it called. A run
killed at its timeout can leave a line cut off mid-JSON; that line is skipped.

Cost is cache-neutral: within one transcript, request i pays the cache-read
price on request i-1's context, the cache-write price on the rest, and the
output price on its output. Real cache state depends on what else ran in the
same hour, so it would make two identical runs cost ten times apart.

No ``from __future__ import annotations``: tests load this file with
importlib and no sys.modules entry, where a dataclass with string annotations
fails to build.
"""

import json
from dataclasses import dataclass
from pathlib import Path

# Opus list price, $ per million tokens.
PRICE = {"input": 5.00, "out": 25.00, "write": 6.25, "read": 0.50}

# Requests that call only these keep aegis's books rather than do the task.
BOOKKEEPING = frozenset(
    {
        "mcp__aegis__turn_end",
        "mcp__aegis__plan_update",
        "mcp__aegis__journal_note",
        "mcp__aegis__toast",
        "mcp__aegis__session_rename",
        "ToolSearch",
    }
)


@dataclass(frozen=True)
class Request:
    transcript: str
    ts: str
    ctx: int  # input + cache write + cache read: the tokens the model processed
    out: int
    tools: tuple[str, ...]


def read_requests(config_dir: Path) -> list[Request]:
    """Every request in the run's transcripts, one per requestId."""
    reqs: dict[str, dict] = {}
    for f in sorted((config_dir / "projects").glob("**/*.jsonl")):
        for line in f.read_text().splitlines():
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("type") != "assistant":
                continue
            m = d.get("message") or {}
            rid = d.get("requestId") or d.get("uuid")
            tools = [
                x["name"] for x in m.get("content") or [] if x.get("type") == "tool_use"
            ]
            if rid in reqs:
                reqs[rid]["tools"] += tools
                continue
            u = m.get("usage") or {}
            ctx = (
                u.get("input_tokens", 0)
                + u.get("cache_creation_input_tokens", 0)
                + u.get("cache_read_input_tokens", 0)
            )
            reqs[rid] = dict(
                transcript=str(f),
                ts=d.get("timestamp", ""),
                ctx=ctx,
                out=u.get("output_tokens", 0),
                tools=tools,
            )
    return [Request(**{**q, "tools": tuple(q["tools"])}) for q in reqs.values()]


def _costs(reqs: list[Request]) -> list[tuple[Request, float]]:
    by_transcript: dict[str, list[Request]] = {}
    for q in sorted(reqs, key=lambda q: q.ts):
        by_transcript.setdefault(q.transcript, []).append(q)
    out = []
    for qs in by_transcript.values():
        prev = 0
        for q in qs:
            hit = min(prev, q.ctx)
            cost = (
                hit * PRICE["read"]
                + (q.ctx - hit) * PRICE["write"]
                + q.out * PRICE["out"]
            ) / 1e6
            out.append((q, cost))
            prev = q.ctx
    return out


def neutral_cost(reqs: list[Request]) -> float:
    return sum(cost for _, cost in _costs(reqs))


def _aegis_only(q: Request) -> bool:
    return bool(q.tools) and all(
        t.startswith("mcp__aegis__") or t in BOOKKEEPING for t in q.tools
    )


def summarize(reqs: list[Request]) -> dict:
    costs = _costs(reqs)
    book = [(q, k) for q, k in costs if q.tools and set(q.tools) <= BOOKKEEPING]
    work = [
        (q, k) for q, k in costs if _aegis_only(q) and not set(q.tools) <= BOOKKEEPING
    ]
    tools: dict[str, int] = {}
    for q in reqs:
        for t in q.tools:
            tools[t] = tools.get(t, 0) + 1
    return dict(
        transcripts=len({q.transcript for q in reqs}),
        requests=len(reqs),
        context=sum(q.ctx for q in reqs),
        output=sum(q.out for q in reqs),
        cost=sum(k for _, k in costs),
        first_ctx=min(reqs, key=lambda q: q.ts).ctx if reqs else 0,
        bookkeeping_requests=len(book),
        bookkeeping_cost=sum(k for _, k in book),
        work_requests=len(work),
        work_cost=sum(k for _, k in work),
        tools=tools,
    )
