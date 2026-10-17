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
import math
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


# Warn when the headline ratio is this much worse than the previous release's.
WORSE = 0.05
# Run a task once more when its ratio moved by more than this share.
MOVED = 0.25


@dataclass
class Comparison:
    rows: list[dict]
    headline: float | None
    failed: list[str]
    rerun: list[str]
    warnings: list[str]
    prev: float | None = None


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def compare(
    stock: dict[str, list[dict]],
    aegis: dict[str, list[dict]],
    prev: dict[str, float] | None,
) -> Comparison:
    """Per-task aegis/stock cost ratios and their geometric mean.

    Each arm maps a task to its runs, each a summarize() dict with ``passed``
    added. A task's cost is the mean over its passing runs; a task with no
    passing run in either arm is listed in ``failed`` and left out of the
    headline, because a failed run's tokens measure the failure. ``prev`` is
    the last release's ``{"headline": r, task: r, ...}``.
    """
    prev = prev or {}
    rows, failed, rerun, ratios = [], [], [], []
    for task in sorted(stock.keys() | aegis.keys()):
        s_runs, a_runs = stock.get(task, []), aegis.get(task, [])
        s_ok = [r for r in s_runs if r["passed"]]
        a_ok = [r for r in a_runs if r["passed"]]
        s_cost = _mean([r["cost"] for r in s_ok])
        a_cost = _mean([r["cost"] for r in a_ok])
        ratio = a_cost / s_cost if s_cost and a_cost is not None else None
        if ratio is None:
            failed.append(task)
        else:
            ratios.append(ratio)
            if task in prev and abs(ratio / prev[task] - 1) > MOVED:
                rerun.append(task)
        book = _mean([r["bookkeeping_cost"] for r in a_ok])
        rows.append(
            dict(
                task=task,
                stock=s_cost,
                aegis=a_cost,
                ratio=ratio,
                stock_requests=_mean([r["requests"] for r in s_ok or s_runs]),
                aegis_requests=_mean([r["requests"] for r in a_ok or a_runs]),
                bookkeeping=book / a_cost if book is not None and a_cost else None,
                stock_passed=bool(s_ok),
                aegis_passed=bool(a_ok),
            )
        )
    headline = math.prod(ratios) ** (1 / len(ratios)) if ratios else None
    warnings = []
    last = prev.get("headline")
    # Rounded: 1.20 - 1.15 is 0.0499... in floating point.
    if headline is not None and last is not None and round(headline - last, 9) >= WORSE:
        warnings.append(
            f"::warning title=aegis token bench::headline {last:.2f} → {headline:.2f}"
        )
    return Comparison(rows, headline, failed, rerun, warnings, last)


def table(cmp: Comparison) -> str:
    """The Markdown table for a release PR."""

    def num(x: float | None, fmt: str = "{:.2f}") -> str:
        return "—" if x is None else fmt.format(x)

    def mark(ok: bool) -> str:
        return "✓" if ok else "✗"

    lines = [
        "| task | stock $ | aegis $ | ratio | requests | bookkeeping | pass |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for r in cmp.rows:
        requests = f"{num(r['stock_requests'], '{:.0f}')} → {num(r['aegis_requests'], '{:.0f}')}"
        lines.append(
            f"| {r['task']} | {num(r['stock'])} | {num(r['aegis'])} | {num(r['ratio'])}"
            f" | {requests} | {num(r['bookkeeping'], '{:.1%}')}"
            f" | {mark(r['stock_passed'])} {mark(r['aegis_passed'])} |"
        )
    n = len(cmp.rows) - len(cmp.failed)
    prev = f"prev {cmp.prev:.2f}" if cmp.prev is not None else ""
    lines.append(f"| geomean ({n}) | | | {num(cmp.headline)} | | | {prev} |")
    return "\n".join(lines)
