# Session attention, slice 3: the recap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a person lands on a tab with a long unread stretch, aegis asks a cheap model for two sentences (what the session was doing, what it needs from you) and shows them as the last row of the transcript; the same recap serves every browser and is paid for once.

**Architecture:** `src/aegis/recap.py` holds everything about one recap: the thresholds, the window of transcript it reads, the prompt and schema, the `claude -p` argv, and the parsing of its JSON envelope. `.aegis.yaml` gains `recap: {agent: <name>}`; nothing defaults. A `Recaps` service on the app runs at most one call per session, never pays twice for the same point in the transcript (`upto`, the store index of the last record), and appends the result as an aegis record `{"kind": "recap", ...}`, which the fold turns into a `recap` entry, folded to one line once the person sends again. The person-only operation `recap.request` is what a browser calls when it opens a tab, or when the sparkle button asks regardless. The fake claude learns `-p` so tests run the real argv path without calling Claude.

**Tech Stack:** Python 3.13, asyncio subprocesses, pydantic; plain ES modules; Playwright.

**Spec:** `docs/superpowers/specs/2026-10-08-session-attention-design.md`, slice 3 ("Recap"), section "The recap".

## Global Constraints

- Thresholds (constants in `recap.py`): a recap is made only if the session is not in a turn, has unread agent messages, and at least 2 are unread, or one unread message is longer than 300 words, or the last read was more than 30 minutes ago. `force` (the sparkle button and the refresh link) skips the thresholds and the "already made for this `upto`" check, but never the "not in a turn" or "recap is off" checks.
- Window: from the last user message or the first unread agent message, whichever is earlier, to the end; at most about 3,000 tokens (12,000 characters, cut from the start); plus the agent's own `turn_end` line and plan from `standing`, which the prompt says to trust over its own reading.
- Output schema: `context` (one sentence, what the session was doing) and `ask` (one sentence, what it needs from you, empty when nothing), in the language of the person's own messages.
- One-shot argv: `<claude_bin> -p <prompt> --model <agent model> --output-format json --json-schema <schema> --system-prompt <SYSTEM> --tools "" --setting-sources "" --mcp-config '{"mcpServers":{}}' --strict-mcp-config`; run from an empty directory under the state root, stdin closed, env `MAX_THINKING_TOKENS=0`; killed if it takes over 60 s.
- Config: `recap: {agent: <name>}` must name an agent in `agents:` whose harness is `claude-code`; anything else makes `recap.request` answer `{"status": "off", "why": ...}` naming the key. Absent `recap:` → off, with "add recap: {agent: <name>} to .aegis.yaml".
- Record: `{"kind": "recap", "upto": int, "context": str, "ask": str, "model": str, "cost_usd": float, "duration_ms": int}`. The fold's `recap` entry carries them in `detail`, plus `folded: bool`, true once a `send` record follows it.
- The recap never reaches the agent: nothing in it is sent to the harness.
- Cost: `Session.recap_cost_usd` (meta and card), apart from `cost_usd`.
- `recap.request` is for people only. Answers: `{"status": "made" | "exists" | "skip" | "busy" | "off" | "failed", ...}`.
- Glyphs are SVG (`g-sparkle` in the sprite), never emoji.
- Commits: conventional, `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`, named paths, no amend. Branch `session-attention-slice-3` in `.claude/worktrees/session-attention-3`.

## Review Focus

- Two browsers landing on the same tab at the same moment: one call, one record, pinned in Task 2.
- A recap request while the agent is mid-turn: answered `busy`, no call, pinned in Task 2.
- A claude that exits non-zero, prints non-JSON, or times out: `failed`, no record, the in-flight slot freed, pinned in Task 2.
- A person's language: Spanish user lines must produce a Spanish recap; the prompt says so and the live test checks it, Task 4.
- A recap stays in the history: once the person sends, it folds to one line and the next turn's entries follow it; the next landing makes a new one. Pinned by the fold test that a `send` folds it (Task 1) and the browser test (Task 3).

---

### Task 1: `recap.py`, the config key, and the fold's recap entry

**Files:**
- Create: `src/aegis/recap.py`, `tests/test_recap.py`
- Modify: `src/aegis/transcript/entries.py`, `src/aegis/transcript/describe.py` (a `RECAP_GLYPH`), `tests/test_fold.py`

**Interfaces:**
- Produces in `recap.py`: `MIN_UNREAD = 2`, `LONG_WORDS = 300`, `AWAY_S = 1800`, `WINDOW_CHARS = 12_000`, `TIMEOUT_S = 60`; `class RecapOut(BaseModel)` with `context: str`, `ask: str`; `SYSTEM: str`; `needed(entries: list[dict], unread: set[str], last_read_at: float | None, now: float) -> bool`; `window(entries: list[dict], unread: set[str], standing: dict) -> str`; `argv(claude_bin: str, model: str, prompt: str) -> list[str]`; `parse(stdout: str) -> tuple[RecapOut | None, float, int]` (value, cost_usd, duration_ms); `load_recap(config_root: Path) -> dict | None` returning `None` when absent, `{"agent": name}` or `{"error": why}`.
- Produces in the fold: `Fold.last_index: int` (index of the last record applied, -1 if none); a `recap` entry `{"kind": "recap", "id": f"e{i}", "glyph": RECAP_GLYPH, "detail": {"context", "ask", "model", "cost_usd", "duration_ms", "upto", "folded"}}`; a later `send` record re-upserts the latest recap with `folded: True`.

- [ ] **Step 1: Write the failing tests**

`tests/test_recap.py`:

```python
import json

from aegis.recap import (
    AWAY_S,
    RecapOut,
    argv,
    load_recap,
    needed,
    parse,
    window,
)
from aegis.transcript.entries import EMPTY_STANDING


def prose(i, text):
    return {"id": f"e{i}.0", "kind": "prose", "md": text}


def user(i, text):
    return {"id": f"e{i}.0", "kind": "user", "md": text}


def test_needed_follows_the_thresholds():
    es = [user(1, "go"), prose(2, "a"), prose(3, "b")]
    now = 10_000.0
    assert not needed(es, set(), None, now)  # nothing unread
    assert not needed(es, {"e3.0"}, now - 60, now)  # one short, recent
    assert needed(es, {"e2.0", "e3.0"}, now - 60, now)  # two unread
    assert needed(es, {"e3.0"}, now - AWAY_S - 1, now)  # away long
    long = [user(1, "go"), prose(2, "word " * 301)]
    assert needed(long, {"e2.0"}, now - 60, now)  # one long


def test_window_starts_at_the_earlier_of_last_user_and_first_unread():
    es = [user(1, "first ask"), prose(2, "old reply"), user(3, "second ask"), prose(4, "new reply")]
    w = window(es, {"e2.0"}, EMPTY_STANDING)
    assert "first ask" not in w and "old reply" in w and "second ask" in w
    w2 = window(es, {"e4.0"}, EMPTY_STANDING)
    assert "old reply" not in w2 and "second ask" in w2 and "new reply" in w2


def test_window_carries_the_agents_report_and_plan_and_is_capped():
    st = {**EMPTY_STANDING, "report": {"attention": "needs_you", "line": "Merge or rebase?", "replies": []},
          "plan": [{"text": "land it", "state": "doing"}]}
    w = window([user(1, "go"), prose(2, "x" * 50_000)], {"e2.0"}, st)
    assert "Merge or rebase?" in w and "land it" in w
    assert len(w) <= 13_000


def test_argv_sheds_tools_settings_and_mcp():
    a = argv("/bin/claude", "claude-haiku-4-5-20251001", "PROMPT")
    assert a[:3] == ["/bin/claude", "-p", "PROMPT"]
    for flag, value in [("--model", "claude-haiku-4-5-20251001"), ("--output-format", "json"),
                        ("--tools", ""), ("--setting-sources", ""), ("--mcp-config", '{"mcpServers": {}}')]:
        assert a[a.index(flag) + 1] == value
    assert "--strict-mcp-config" in a
    assert json.loads(a[a.index("--json-schema") + 1])["properties"].keys() == {"context", "ask"}
    assert a[a.index("--system-prompt") + 1]


def test_parse_reads_the_envelope_and_never_raises():
    out = json.dumps({"result": '{"context": "fixing the archive", "ask": "merge or rebase?"}',
                      "total_cost_usd": 0.004, "duration_ms": 1800})
    v, cost, ms = parse(out)
    assert v == RecapOut(context="fixing the archive", ask="merge or rebase?") and cost == 0.004 and ms == 1800
    so = json.dumps({"structured_output": {"context": "c", "ask": ""}, "result": "", "total_cost_usd": 0.001, "duration_ms": 5})
    assert parse(so)[0] == RecapOut(context="c", ask="")
    assert parse("not json") == (None, 0.0, 0)
    assert parse(json.dumps({"result": "no object here"}))[0] is None


def test_load_recap_names_an_agent_or_says_what_is_wrong(tmp_path):
    assert load_recap(tmp_path) is None
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n  haiku: {harness: claude-code, model: m, effort: low, permission: read}\n"
        "  oc: {harness: opencode, model: a/b, effort: low, permission: read}\n"
        "recap: {agent: haiku}\n"
    )
    assert load_recap(tmp_path) == {"agent": "haiku", "model": "m"}
    (tmp_path / ".aegis.yaml").write_text("agents: {}\nrecap: {agent: nope}\n")
    assert "nope" in load_recap(tmp_path)["error"]
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n  oc: {harness: opencode, model: a/b, effort: low, permission: read}\nrecap: {agent: oc}\n")
    assert "claude-code" in load_recap(tmp_path)["error"]
    (tmp_path / ".aegis.yaml").write_text("recap: {}\n")
    assert "agent" in load_recap(tmp_path)["error"]
```

`tests/test_fold.py` (append):

```python
def test_a_recap_record_is_an_entry_and_a_send_folds_it():
    rec = Rec()
    rec.own("send", text="go")
    rec.echo("go")
    rec.text("done it")
    rec.result()
    rec.own("recap", upto=3, context="fixing it", ask="merge?", model="m", cost_usd=0.004, duration_ms=1800)
    f, _ = run(rec)
    (r,) = [e for e in f.entries() if e["kind"] == "recap"]
    assert r["id"] == "e4" and r["detail"]["context"] == "fixing it" and r["detail"]["folded"] is False
    assert f.last_index == 4
    ops = f.apply(rec.own("send", text="next"))
    assert any(op.get("upsert", {}).get("id") == "e4" and op["upsert"]["detail"]["folded"] for op in ops)
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -q tests/test_recap.py tests/test_fold.py -k "recap or needed or window or argv or parse or load_recap"`
Expected: FAIL (`ModuleNotFoundError: aegis.recap`).

- [ ] **Step 3: Implement**

`src/aegis/recap.py`:

```python
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
import time
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from .agents import ConfigError, load_agents, read_config

MIN_UNREAD = 2
LONG_WORDS = 300
AWAY_S = 1800
WINDOW_CHARS = 12_000
TIMEOUT_S = 60


class RecapOut(BaseModel):
    context: str = Field(description="One sentence: what the session was working on, at the level of the goal.")
    ask: str = Field(description="One sentence: what it needs from the person now. Empty if nothing.")


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
    "sentence of at most 25 words. `ask` is one sentence saying what the person "
    "must answer, decide or read, and is empty when the session needs nothing from "
    "them. No preamble, no praise."
)


def needed(entries: list[dict], unread: set[str], last_read_at: float | None, now: float) -> bool:
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
        return f"tool: {e.get('title', '')} {e.get('summary', '')} -> {(e.get('detail') or {}).get('result', '')}".strip()
    if e["kind"] == "inbox":
        return f"message to the agent: {e.get('title', '')}"
    return None


def window(entries: list[dict], unread: set[str], standing: dict) -> str:
    users = [k for k, e in enumerate(entries) if e["kind"] == "user"]
    firsts = [k for k, e in enumerate(entries) if e["kind"] == "prose" and e["id"] in unread]
    starts = [k for k in (users[-1] if users else None, firsts[0] if firsts else None) if k is not None]
    start = min(starts) if starts else 0
    body = "\n".join(x for e in entries[start:] if (x := _line(e)))
    body = body[-WINDOW_CHARS:]
    parts = [f"--- transcript ---\n{body}\n--- end ---"]
    report = standing.get("report")
    if report:
        parts.append(f"AGENT REPORT ({report['attention']}): {report['line']}")
    plan = standing.get("plan") or []
    if plan:
        parts.append("PLAN:\n" + "\n".join(f"- [{i['state']}] {i['text']}" for i in plan))
    return "\n\n".join(parts)


def argv(claude_bin: str, model: str, prompt: str) -> list[str]:
    return [
        claude_bin, "-p", prompt,
        "--model", model,
        "--output-format", "json",
        "--json-schema", json.dumps(RecapOut.model_json_schema()),
        "--system-prompt", SYSTEM,
        "--tools", "",
        "--setting-sources", "",
        "--mcp-config", json.dumps({"mcpServers": {}}),
        "--strict-mcp-config",
    ]


_OBJ = re.compile(r"\{.*\}", re.S)


def parse(stdout: str) -> tuple[RecapOut | None, float, int]:
    """The envelope's structured answer, cost and time. Never raises."""
    try:
        env = json.loads(stdout)
    except (ValueError, TypeError):
        return None, 0.0, 0
    if not isinstance(env, dict):
        return None, 0.0, 0
    cost = float(env.get("total_cost_usd") or 0.0)
    ms = int(env.get("duration_ms") or 0)
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
        return {"error": f"recap: agent {name!r} runs {agent.harness}; a recap needs a claude-code agent"}
    if agent.error:
        return {"error": f"recap: agent {name!r}: {agent.error}"}
    return {"agent": name, "model": agent.model}
```

`src/aegis/transcript/describe.py`: `RECAP_GLYPH = "✦"` next to the other glyphs (the client draws an SVG; the Python glyph is the data's fallback, like the others).

`src/aegis/transcript/entries.py`: in `Fold.__init__`, `self.last_index = -1` and `self._recap: str | None = None`. In `apply`, set `self.last_index = record["i"]` first. In `_own`, the `send` branch (after its `_stand`) re-upserts the latest recap folded: build `ops` for it and prepend them to the branch's return value:

```python
            folded: list[dict] = []
            r = self._entries.get(self._recap) if self._recap else None
            if r is not None and not r["detail"].get("folded"):
                folded = self._upsert({**r, "detail": {**r["detail"], "folded": True}})
```

and return `folded + <the existing upsert>`. A new branch before `interrupt`:

```python
        if kind == "recap":
            self._recap = f"e{i}"
            return self._upsert(
                _entry(
                    f"e{i}", "recap", "ok", ts, d.RECAP_GLYPH, title="recap",
                    summary=str(rec.get("context") or ""),
                    detail={
                        "context": rec.get("context") or "",
                        "ask": rec.get("ask") or "",
                        "model": rec.get("model") or "",
                        "cost_usd": float(rec.get("cost_usd") or 0.0),
                        "duration_ms": int(rec.get("duration_ms") or 0),
                        "upto": rec.get("upto"),
                        "folded": False,
                    },
                )
            )
```

`Fold` also exposes `last_recap_upto: int | None` (the latest recap's `upto`) for the service: set it in that branch.

- [ ] **Step 4: Run them to see them pass**

Run: `uv run pytest -q tests/test_recap.py tests/test_fold.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/recap.py src/aegis/transcript/entries.py src/aegis/transcript/describe.py tests/test_recap.py tests/test_fold.py
git commit -m "feat(recap): thresholds, window, prompt, argv and the fold's recap entry (#171)"
```

---

### Task 2: The fake claude's `-p`, the `Recaps` service and `recap.request`

**Files:**
- Modify: `tests/fake_claude.py` (one-shot mode), `src/aegis/app.py` (service wiring and the operation), `src/aegis/session.py` (`recap_cost_usd`, `add_recap_cost`), `src/aegis/registry.py` (pass it from the meta)
- Create: `src/aegis/recaps.py` (the service), `tests/test_recap_e2e.py`

**Interfaces:**
- Consumes: Task 1's `recap.py` and the fold's `last_index`, `last_recap_upto`; slice 2's `Session.unread`, `last_read_at`, `view()`, `standing`, `in_turn`, `report(record)`.
- Produces: `Recaps(app)` with `async request(session, force: bool) -> dict`; `Session.recap_cost_usd: float` in meta and card; operation `recap.request` `{log_id: str, force: bool = False}` (people only).

- [ ] **Step 1: The fake claude's one-shot mode** (`tests/fake_claude.py`)

At the top of `main()`, before reading stdin:

```python
    if "-p" in sys.argv:
        oneshot()
        return
```

and the function (documented in the module docstring's list as "`-p PROMPT` one-shot: prints a JSON envelope whose structured output is a recap built from the prompt; `FAKE_CLAUDE_ONESHOT=fail|garbage|slow` makes it exit 1, print non-JSON, or sleep 120 s"):

```python
def oneshot() -> None:
    mode = os.environ.get("FAKE_CLAUDE_ONESHOT", "")
    prompt = sys.argv[sys.argv.index("-p") + 1]
    if mode == "fail":
        sys.exit(1)
    if mode == "garbage":
        print("not json")
        return
    if mode == "slow":
        time.sleep(120)
    lines = [l for l in prompt.splitlines() if l.startswith("user: ")]
    last = lines[-1].removeprefix("user: ") if lines else "nothing"
    out = {"context": f"recap of: {last}", "ask": "answer it" if "?" in prompt else ""}
    print(json.dumps({"type": "result", "result": json.dumps(out), "structured_output": out,
                      "total_cost_usd": 0.004, "duration_ms": 1800}))
```

- [ ] **Step 2: Write the failing tests** (`tests/test_recap_e2e.py`, reusing `World`, `mcp`, `turn` from `tests/test_agents.py`; the world's `.aegis.yaml` adds `recap: {agent: opus}`)

```python
import asyncio

import pytest

from aegis.ops import Caller, OpError

from .conftest import until
from .test_agents import CONFIG, World, turn


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG + "recap: {agent: opus}\n")
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


async def two_unread(world):
    a = await world.spawn()
    await turn(a, "first ask")
    await turn(a, "second ask?")
    assert len(a.unread) >= 2
    return a


async def test_a_long_unread_stretch_gets_one_recap_entry_and_its_cost(world):
    a = await two_unread(world)
    r = await world.app.registry.call("recap.request", {"log_id": a.log_id})
    assert r["status"] == "made"
    (e,) = [x for x in a.entries() if x["kind"] == "recap"]
    assert e["detail"]["context"].startswith("recap of:") and e["detail"]["cost_usd"] == 0.004
    assert a.wire()["recap_cost_usd"] == 0.004
    again = await world.app.registry.call("recap.request", {"log_id": a.log_id})
    assert again["status"] == "exists"
    forced = await world.app.registry.call("recap.request", {"log_id": a.log_id, "force": True})
    assert forced["status"] == "made"


async def test_two_requests_at_once_make_one_call(world):
    a = await two_unread(world)
    r1, r2 = await asyncio.gather(
        world.app.registry.call("recap.request", {"log_id": a.log_id}),
        world.app.registry.call("recap.request", {"log_id": a.log_id}),
    )
    assert {r1["status"], r2["status"]} <= {"made", "exists"}
    assert len([x for x in a.entries() if x["kind"] == "recap"]) == 1


async def test_skip_busy_off_and_people_only(world, tmp_path):
    a = await world.spawn()
    assert (await world.app.registry.call("recap.request", {"log_id": a.log_id}))["status"] == "skip"
    await a.send("/sleep 2")
    await until(lambda: a.status == "working", what="working")
    assert (await world.app.registry.call("recap.request", {"log_id": a.log_id, "force": True}))["status"] == "busy"
    with pytest.raises(OpError) as e:
        await world.app.registry.call("recap.request", {"log_id": a.log_id}, Caller("agent", a.log_id))
    assert e.value.code == "not_for_agents"
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    off = await world.app.registry.call("recap.request", {"log_id": a.log_id, "force": True})
    assert off["status"] in ("off", "busy")


@pytest.mark.parametrize("mode", ["fail", "garbage"])
async def test_a_broken_call_is_failed_and_frees_the_slot(world, mode, monkeypatch):
    a = await two_unread(world)
    monkeypatch.setenv("FAKE_CLAUDE_ONESHOT", mode)
    r = await world.app.registry.call("recap.request", {"log_id": a.log_id})
    assert r["status"] == "failed"
    assert not [x for x in a.entries() if x["kind"] == "recap"]
    monkeypatch.delenv("FAKE_CLAUDE_ONESHOT")
    assert (await world.app.registry.call("recap.request", {"log_id": a.log_id}))["status"] == "made"
```

(The service builds the subprocess environment from `os.environ` at call time, so `monkeypatch.setenv` reaches the fake claude; the fixture's wrapper script exports `FAKE_CLAUDE_HOME` itself.)

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest -q tests/test_recap_e2e.py`
Expected: FAIL (`unknown_op`).

- [ ] **Step 4: Implement**

`src/aegis/session.py`: `recap_cost_usd: float = 0.0` keyword and attribute (the registry passes `recap_cost_usd=meta.get("recap_cost_usd") or 0.0` when it builds a session from a meta); in `meta()`, so the card carries it; `add_recap_cost(cost)` as below.

`src/aegis/recaps.py`:

```python
"""Runs recaps: one call per session at a time, never twice for the same point
in the transcript unless asked, and the result appended as an aegis record so
every browser gets the same entry. Rules of one recap are in recap.py."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from . import recap


class Recaps:
    def __init__(self, app) -> None:
        self.app = app
        self._running: dict[str, asyncio.Task] = {}

    async def request(self, s, force: bool) -> dict:
        cfg = recap.load_recap(self.app.roots.config_root)
        if cfg is None:
            return {"status": "off", "why": "add recap: {agent: <name>} to .aegis.yaml"}
        if "error" in cfg:
            return {"status": "off", "why": cfg["error"]}
        if s.in_turn:
            return {"status": "busy"}
        if s.log_id in self._running:
            return await asyncio.shield(self._running[s.log_id])
        fold = s.fold()
        upto = fold.last_index
        if not force:
            if fold.last_recap_upto == upto:
                return {"status": "exists"}
            if not recap.needed(fold.entries(), s.unread, s.last_read_at, time.time()):
                return {"status": "skip"}
        task = asyncio.get_running_loop().create_task(self._make(s, cfg, upto))
        self._running[s.log_id] = task
        try:
            return await asyncio.shield(task)
        finally:
            if self._running.get(s.log_id) is task:
                del self._running[s.log_id]

    async def _make(self, s, cfg: dict, upto: int) -> dict:
        prompt = recap.window(s.fold().entries(), s.unread, s.standing)
        cwd = self.app.roots.state_root / "oneshot"
        cwd.mkdir(parents=True, exist_ok=True)
        try:
            proc = await asyncio.create_subprocess_exec(
                *recap.argv(self.app.claude_bin, cfg["model"], prompt),
                cwd=cwd,
                env={**os.environ, "MAX_THINKING_TOKENS": "0"},
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError as e:
            return {"status": "failed", "why": str(e)}
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), recap.TIMEOUT_S)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {"status": "failed", "why": f"no answer in {recap.TIMEOUT_S}s"}
        if proc.returncode != 0:
            return {"status": "failed", "why": f"claude exited with code {proc.returncode}"}
        value, cost, ms = recap.parse(out.decode(errors="replace"))
        if value is None:
            return {"status": "failed", "why": "the model returned nothing usable"}
        s.report({"kind": "recap", "upto": upto, "context": value.context, "ask": value.ask,
                  "model": cfg["model"], "cost_usd": cost, "duration_ms": ms})
        s.add_recap_cost(cost)
        return {"status": "made", "context": value.context, "ask": value.ask}
```

`Session.add_recap_cost(cost: float)` (in session.py, next to `report`): `self._set(recap_cost_usd=round(self.recap_cost_usd + cost, 6))`.

`src/aegis/app.py`: `self.recaps = Recaps(self)` in `__init__`; operation next to `session.read`:

```python
class RecapParams(_Strict):
    log_id: str
    force: bool = False

        @r.op("recap.request", RecapParams)
        async def recap_request(p: RecapParams, caller):
            """A recap of where the session stands, for a person landing on its tab."""
            return await self.recaps.request(reg.open(p.log_id), p.force)
```

- [ ] **Step 5: Run them to see them pass**

Run: `uv run pytest -q tests/test_recap_e2e.py tests/test_recap.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/fake_claude.py src/aegis/recaps.py src/aegis/app.py src/aegis/session.py tests/test_recap_e2e.py
git commit -m "feat(recap): recap.request runs one cheap claude -p per landing and records it for every browser (#171)"
```

---

### Task 3: The recap row, the sparkle button and the request on landing

**Files:**
- Modify: `src/aegis/client/js/glyphs.js` (`g-sparkle`), `src/aegis/client/js/entries.js` (the `recap` renderer), `src/aegis/client/js/app.js`, `src/aegis/client/index.html`, `src/aegis/client/css/base.css`
- Test: `tests/test_browser.py`

**Interfaces:**
- Consumes: `recap` entries (Task 1); `recap.request` (Task 2).
- Produces: the recap row; `#nav-recap` button.

- [ ] **Step 1: Write the failing browser test**

The browser fixture's `.aegis.yaml` must name a recap agent for this test: add a `recap_server` fixture next to `server` that writes `CONFIG + "recap: {agent: opus}\n"` (the fake claude answers `-p`).

```python
def test_landing_after_two_replies_shows_a_recap_last_and_the_sparkle_makes_one(recap_server, page):
    page.goto(recap_server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "first")
    sid = page.evaluate("location.hash.slice(3)")
    page.wait_for_function("() => !document.querySelector('.row.prose .rm .ic.unread')", timeout=6000)
    page.fill("#input", "/sleep 1")
    page.press("#input", "Enter")
    page.click("#tab-fleet")
    page.wait_for_selector(f".card[data-id='{sid}'] .ft >> text=1 unread", timeout=8000)
    # one unread is under the threshold: make a second one land while away
    page.click(f".tab[data-id='{sid}']")
    page.fill("#input", "/sleep 1")
    page.press("#input", "Enter")
    page.click("#tab-fleet")
    page.wait_for_selector(f".card[data-id='{sid}'] .ft >> text=2 unread", timeout=8000)
    page.click(f".tab[data-id='{sid}']")
    page.wait_for_selector(".row.recap .ctx >> text=recap of", timeout=8000)
    assert page.eval_on_selector("#entries", "n => n.lastElementChild.classList.contains('recap')")
    page.fill("#input", "thanks")
    page.press("#input", "Enter")
    page.wait_for_selector(".row.recap.folded")
    page.click("#nav-recap")
    page.wait_for_function("() => document.querySelectorAll('.row.recap').length === 2", timeout=8000)
    assert page.errors == []
```

If the second `/sleep 1` reply is read before the Fleet click (the reader is on the tab), send it and switch to the Fleet in the same step as the first; the test needs two unread when it lands. Keep the assertions.

- [ ] **Step 2: Run it to see it fail**

Run: `uv run pytest -q -m browser tests/test_browser.py -k recap`
Expected: FAIL waiting for `.row.recap`.

- [ ] **Step 3: Implement**

`glyphs.js` sprite: `<symbol id="g-sparkle" viewBox="0 0 16 16" fill="currentColor"><path d="M7 1.8l1.2 3.4 3.4 1.2-3.4 1.2L7 11 5.8 7.6 2.4 6.4l3.4-1.2z"/><path d="M12.2 9.6l.6 1.6 1.6.6-1.6.6-.6 1.6-.6-1.6-1.6-.6 1.6-.6z"/></symbol>`.

`entries.js`:

```javascript
  recap(e) {
    const det = e.detail || {};
    const body = el("div", "body");
    if (det.folded) {
      body.append(el("span", "lbl", "recap · "), el("span", "ctx", det.context));
      const r = row(e, "recap folded", body);
      r.querySelector(".g").replaceChildren(icon("sparkle"));
      return r;
    }
    const hd = el("div", "hd");
    hd.append(icon("sparkle"), el("span", null, det.ask ? "recap · needs you" : "recap"));
    body.append(hd, el("div", "ctx", det.context));
    if (det.ask) body.append(el("div", "ask", det.ask));
    const ft = el("div", "ft");
    const secs = det.duration_ms ? `${(det.duration_ms / 1000).toFixed(1)}s` : "";
    ft.append(el("span", null, [det.model, secs, det.cost_usd ? `$${det.cost_usd.toFixed(4)}` : ""].filter(Boolean).join(" · ")));
    const again = el("button", "btn link refresh", "refresh");
    again.dataset.recap = "force";
    ft.append(again);
    body.append(ft);
    const r = row(e, "recap", body);
    r.querySelector(".g").replaceChildren(icon("sparkle"));
    return r;
  },
```

`index.html`: in `#nav`, before `#nav-up`: `<button class="spark" id="nav-recap" title="Recap where this session stands"></button>`.

`app.js`:
- fill `#nav-recap` with `icon("sparkle")`; click → `askRecap(true)`; a delegated click on `#entries` for `[data-recap=force]` → `askRecap(true)`;
- `askRecap(force)`: `conn.call("recap.request", { log_id: shown, force })`; when `status === "off"`, show `why` in `#send-error`; when `failed`, show `why` there too; otherwise nothing (the entry arrives on the transcript channel);
- in `follow()`, on the first snapshot after a switch (the same place slice 2 calls `setSince`), call `askRecap(false)` — the server decides whether it is worth it.

`base.css`:

```css
/* the recap: aegis's own two sentences, last in the transcript */
#a2 .row.recap .g{color:var(--accent)}
#a2 .row.recap .g .ic{width:14px;height:14px}
#a2 .row.recap:not(.folded) .body{border:1px solid var(--accent);border-left-width:3px;border-radius:var(--r);background:var(--accent-soft);padding:10px 14px}
#a2 .row.recap .hd{display:flex;gap:8px;align-items:center;font-family:var(--font-chrome);font-size:11.5px;color:var(--accent);margin-bottom:6px}
#a2 .row.recap .hd .ic{width:13px;height:13px}
#a2 .row.recap .ctx{font-size:14.5px;color:var(--strong);line-height:1.5}
#a2 .row.recap .ask{font-size:14.5px;color:var(--strong);line-height:1.5;margin-top:6px;font-weight:500}
#a2 .row.recap .ft{font-family:var(--font-mono);font-size:11px;color:var(--faint);margin-top:8px;display:flex;gap:10px;align-items:center}
#a2 .row.recap .btn.link{background:none;border:none;padding:0;color:var(--muted);text-decoration:underline;font-size:11px}
#a2 .row.recap.folded .body{font-size:12.5px;color:var(--muted)}
#a2 .row.recap.folded .lbl{color:var(--accent);font-family:var(--font-chrome)}
#a2 .nav .spark{color:var(--accent)}
```

- [ ] **Step 4: Run the touched browser tests**

Run: `uv run pytest -q -m browser tests/test_browser.py -k "recap or divider or navigator or read_on_screen or question_mark"`
Expected: PASS, none skipped.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/client/js/glyphs.js src/aegis/client/js/entries.js src/aegis/client/js/app.js src/aegis/client/index.html src/aegis/client/css/base.css tests/test_browser.py
git commit -m "feat(client): the recap row last in the transcript, a sparkle to ask, and a request on landing (#171)"
```

---

### Task 4: A live recap with real Haiku

**Files:** `tests/test_live.py`

- [ ] **Step 1: Write the live test** in the style of `test_real_claude_reports_its_turns_with_turn_end` (uvicorn, an App on a tmp root): `.aegis.yaml` with a `sonnet` agent for the session and `haiku` (`HAIKU`) for `recap: {agent: haiku}`. Send two Spanish prompts that each get a short answer (e.g. "Explícame en dos frases qué es un rebase." and "¿Y un merge?"), then call `recap.request` with `force: True`. Assert status `made`, a `recap` entry whose `context` is non-empty and in Spanish (contains at least one of " el ", " la ", " de ", " que "), cost > 0, and `recap_cost_usd` on the card equal to it.

- [ ] **Step 2: Run it** (spends a few cents): `uv run pytest -q --run-live -m live tests/test_live.py -k recap` — report the result and the recap text.

- [ ] **Step 3: Commit**

```bash
git add tests/test_live.py
git commit -m "test(live): a real Haiku recap of a Spanish session, in Spanish (#171)"
```

---

### Task 5 (controller): docs, gates, bench, PR

- DESIGN.md: a paragraph on the recap (people-only, one call per session, `upto`, a record every browser receives, never reaches the agent).
- `changelog.d/171-session-attention-recap.added.md`.
- Spec status: all three slices implemented.
- Gates on a clean copy, the visual check in three themes, the bench against `main`, the live test, PR.
