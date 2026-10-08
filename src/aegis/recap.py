"""A recap: two sentences for a person who comes back to a tab after a while.

The browser asks when it opens a tab; the server decides whether the unread
stretch is long enough (``needed``), builds the window the model reads
(``window``), and runs one cheap ``claude -p`` (``argv``, ``parse``). Every flag
in the argv was measured in the legacy driver (legacy/aegis/drivers/claude.py,
``_oneshot_argv``): ``--tools ""`` and ``--system-prompt`` stop the call going
agentic, ``--setting-sources ""`` and an empty cwd shed the project's
instructions (21,445 -> 7,749 input tokens), and thinking off saves seconds a
two-sentence answer does not need. The recap is aegis talking to the person;
nothing here reaches the agent.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from .agents import ConfigError, load_agents, read_config

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


def argv(claude_bin: str, model: str, prompt: str) -> list[str]:
    return [
        claude_bin,
        "-p",
        "--model",
        model,
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(RecapOut.model_json_schema()),
        "--system-prompt",
        SYSTEM,
        "--tools",
        "",
        "--setting-sources",
        "",
        "--mcp-config",
        json.dumps({"mcpServers": {}}),
        "--strict-mcp-config",
        # Last and after --: the window opens on "---", which the CLI would
        # otherwise reject as an unknown option.
        "--",
        prompt,
    ]


_OBJ = re.compile(r"\{.*\}", re.S)


def _number(value, kind: type[float] | type[int]) -> float | int:
    """A bad cost or duration reads as 0, so the recap still lands."""
    try:
        return kind(value or 0)
    except (TypeError, ValueError, OverflowError):
        return kind(0)


def parse(stdout: str) -> tuple[RecapOut | None, float, int]:
    """The envelope's structured answer, cost and time. Never raises."""
    try:
        env = json.loads(stdout)
    except (ValueError, TypeError):
        return None, 0.0, 0
    if not isinstance(env, dict):
        return None, 0.0, 0
    cost = _number(env.get("total_cost_usd"), float)
    ms = int(_number(env.get("duration_ms"), int))
    candidates = []
    if isinstance(env.get("structured_output"), dict):
        candidates.append(env["structured_output"])
    text = str(env.get("result") or "")
    for raw in (text, *(_OBJ.findall(text))):
        try:
            candidates.append(json.loads(raw))
        except (ValueError, TypeError):
            pass
    for c in candidates:
        if isinstance(c, dict):
            try:
                return RecapOut.model_validate(c), cost, ms
            except ValidationError:
                continue
    return None, cost, ms


def load_recap(config_root: Path) -> dict | None:
    """``recap: {agent: <name>}``: None when absent, else the agent or why not.
    Nothing defaults (agents.py)."""
    try:
        raw = read_config(config_root).get("recap")
    except ConfigError as e:
        return {"error": str(e)}
    if raw is None:
        return None
    name = raw.get("agent") if isinstance(raw, dict) else None
    if not name:
        return {"error": "recap: needs an agent: recap: {agent: <name>}"}
    agent = next((a for a in load_agents(config_root) if a.name == name), None)
    if agent is None:
        return {"error": f"recap: names agent {name!r}, which agents: does not define"}
    if agent.harness != "claude-code":
        return {
            "error": f"recap: agent {name!r} runs {agent.harness}; a recap needs a claude-code agent"
        }
    if agent.error:
        return {"error": f"recap: agent {name!r}: {agent.error}"}
    return {"agent": name, "model": agent.model}
