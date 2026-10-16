"""The same aegis tasks, by real Claude sessions, through the MCP tools and
through `aegis call` (#436). Spends real quota: about $4 at two reps.

Each task runs in three arms: `mcp` adds nothing to the prompt, `cli` says
`aegis call` exists, `cli-told` asks for it. Every session is fresh, on an
isolated server in a temporary root.

    uv run python scripts/bench_call.py [reps]

Prints one JSON line per run and writes results.json and transcripts.json
into the temporary root.
"""

import asyncio
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

import uvicorn

from aegis.app import App
from aegis.roots import make_roots
from aegis.web import build_web

MODEL = "claude-sonnet-5"
REPS = int(sys.argv[1]) if len(sys.argv) > 1 else 2

QUESTIONS = [
    "how do I wait for a long build without polling",
    "how do I hand work to another agent",
    "how do I send a file to the person",
    "how do I create an interactive artifact page",
    "how do I enqueue a task for a worker",
    "how do I schedule a recurring run",
    "how do I wait until other sessions finish",
    "how do I link another aegis server",
    "how do I record a decision in the journal",
    "how do I end my turn and ask a question",
    "how do I keep a plan visible",
    "how do I read what a peer session is doing",
]
NOTES = 15

CLI_HINT = """

You can also call any aegis tool from bash, with exactly the same semantics,
identity and permissions as the MCP tool: `aegis call <tool> key=value ...`
(a value is read as JSON when it parses, or pass one JSON object, or `-` to
read it from stdin), and `aegis tools [tool]` lists the tools or prints one
tool's schema. Use whichever is more convenient."""

CLI_TOLD = CLI_HINT.replace(
    "Use whichever is more convenient.",
    "For this task, make the aegis calls through `aegis call` in bash, and "
    "batch them in loops and pipes rather than one call per tool use.",
)
ARMS = {"mcp": "", "cli": CLI_HINT, "cli-told": CLI_TOLD}

TASKS = {
    "help-fanout": (
        "For each of these questions about aegis, find the single most relevant "
        "help section ref (`page#section`) with aegis's help search. Reply with a "
        "Markdown table with columns `question` and `ref`, one row per question, "
        "in this order, questions copied verbatim:\n"
        + "\n".join(f"- {q}" for q in QUESTIONS)
    ),
    "journal-fanout": (
        f"The directory notes/ holds {NOTES} files. For each file, record one aegis "
        "journal note with tag `milestone`, whose text is the file's first line "
        "verbatim and whose paths is a list with that file's path (notes/<name>). "
        "Then reply with the number of notes you recorded."
    ),
    "help-wordcount": (
        "For every aegis help page, find how many words its full Markdown has "
        "(as `wc -w` counts the text help_read returns for the page id). Reply "
        "with a Markdown table with columns `page` and `words`, one row per page."
    ),
}


async def until(pred, timeout, what):
    t0 = time.monotonic()
    while not pred():
        if time.monotonic() - t0 > timeout:
            raise TimeoutError(what)
        await asyncio.sleep(1)


def setup(root: Path) -> None:
    (root / ".aegis.yaml").write_text(
        f"agents:\n  bench: {{harness: claude-code, model: {MODEL}, effort: medium, permission: full}}\n"
    )
    notes = root / "notes"
    notes.mkdir()
    for i in range(NOTES):
        (notes / f"note-{i:02d}.md").write_text(
            f"Milestone {i:02d}: finished part {chr(65 + i)} of the bench\nbody\n"
        )


def tool_breakdown(entries, aegis_tools: set[str]) -> dict:
    out = {"aegis_mcp": 0, "bash": 0, "toolsearch": 0, "other": 0}
    for e in entries:
        if e["kind"] != "tool":
            continue
        name = e.get("title") or ""
        if name.startswith("mcp__aegis__"):
            name = name.removeprefix("mcp__aegis__")
        if name in aegis_tools:
            out["aegis_mcp"] += 1
        elif name == "Bash":
            out["bash"] += 1
            if "aegis call" in json.dumps(e.get("detail")):
                out["bash_aegis"] = out.get("bash_aegis", 0) + 1
        elif "ToolSearch" in name:
            out["toolsearch"] += 1
        else:
            out["other"] += 1
    return out


def journal_notes(state_root: Path, log_id: str) -> list[dict]:
    found = []
    for p in state_root.rglob(f"*{log_id}*"):
        if p.is_file() and p.suffix == ".jsonl":
            for line in p.read_text().splitlines():
                if '"journal_note"' in line:
                    found.append(json.loads(line))
    return found


async def run_one(app, task, arm, rep, aegis_tools, truth):
    r = await app.registry.call("session.spawn", {"agent": "bench"})
    s = app.sessions.sessions[r["log_id"]]
    prompt = TASKS[task] + ARMS[arm]
    t0 = time.monotonic()
    await s.send(prompt)
    await until(lambda: s.status == "working", 60, "start")
    await until(lambda: s.status == "idle", 900, f"{task}/{arm}/{rep}")
    wall = time.monotonic() - t0
    entries = s.entries()
    answer = "\n".join(e.get("md") or "" for e in entries if e["kind"] == "prose")
    if task == "help-fanout":
        correct = sum(1 for q, ref in truth["refs"].items() if ref in answer)
        total = len(truth["refs"])
    elif task == "help-wordcount":
        import re

        rows = dict(re.findall(r"\|\s*`?([\w.-]+)`?\s*\|\s*([\d,]+)\s*\|", answer))
        got = {k: int(v.replace(",", "")) for k, v in rows.items()}
        correct = sum(
            1
            for k, n in truth["words"].items()
            if k in got and abs(got[k] - n) <= max(3, n * 0.02)
        )
        total = len(truth["words"])
    else:
        notes = journal_notes(app.roots.state_root, s.log_id)
        want = {
            f"Milestone {i:02d}: finished part {chr(65 + i)} of the bench"
            for i in range(NOTES)
        }
        got = {n.get("text") for n in notes}
        correct, total = len(want & got), NOTES
    tools = tool_breakdown(entries, aegis_tools)
    res = {
        "task": task,
        "arm": arm,
        "rep": rep,
        "wall_s": round(wall, 1),
        "cost_usd": round(s.cost_usd or 0, 4),
        "context_tokens": s.context_tokens,
        "tool_entries": sum(tools.values()),
        **tools,
        "correct": f"{correct}/{total}",
        "log_id": s.log_id,
    }
    print(json.dumps(res), flush=True)
    await s.stop()
    return res, entries


async def main():
    claude = shutil.which("claude")
    aegis = shutil.which("aegis")
    assert claude and aegis, (claude, aegis)
    root = Path(tempfile.mkdtemp(prefix="aegis-bench-call-"))
    setup(root)
    import socket

    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        port = so.getsockname()[1]
    app = App(
        make_roots(root, None), claude_bin=claude, base_url=f"http://127.0.0.1:{port}"
    )
    server = uvicorn.Server(
        uvicorn.Config(
            build_web(app, "t", {f"127.0.0.1:{port}"}), port=port, log_level="warning"
        )
    )
    srv = asyncio.create_task(server.serve())
    await until(lambda: server.started, 10, "uvicorn")
    aegis_tools = {op.tool_name for op in app.registry.agent_ops()}
    truth = {"refs": {}, "words": {}}
    for q in QUESTIONS:
        hits = await app.registry.call("help.search", {"query": q, "limit": 1})
        truth["refs"][q] = hits["hits"][0]["ref"] if hits.get("hits") else "?"
    from aegis.help_ops import Index, load

    for page in Index(load()).pages:
        text = await app.registry.call("help.read", {"page": page})
        truth["words"][page] = len(text.split())
    print("truth", json.dumps(truth), flush=True)
    out = root / "results.json"
    results, transcripts = [], {}
    try:
        jobs = [
            run_one(app, t, a, k, aegis_tools, truth)
            for k in range(REPS)
            for t in TASKS
            for a in ARMS
        ]
        for res, entries in await asyncio.gather(*jobs, return_exceptions=False):
            results.append(res)
            transcripts[f"{res['task']}/{res['arm']}/{res['rep']}"] = entries
        print(
            f"{'task':16}{'arm':10}{'wall_s':>8}{'cost':>8}{'ctx_k':>7}{'tools':>7}  correct"
        )
        for t in TASKS:
            for a in ARMS:
                rs = [r for r in results if r["task"] == t and r["arm"] == a]
                m = lambda k: sum(r[k] or 0 for r in rs) / len(rs)  # noqa: E731
                print(
                    f"{t:16}{a:10}{m('wall_s'):8.0f}{m('cost_usd'):8.3f}"
                    f"{m('context_tokens') / 1000:7.1f}{m('tool_entries'):7.1f}  "
                    + " ".join(r["correct"] for r in rs)
                )
        print(f"results in {out}")
    finally:
        out.write_text(
            json.dumps(
                {"root": str(root), "truth": truth, "results": results}, indent=1
            )
        )
        (root / "transcripts.json").write_text(
            json.dumps(transcripts, indent=1, default=str)
        )
        server.should_exit = True
        await asyncio.wait_for(srv, 30)


if __name__ == "__main__":
    asyncio.run(main())
