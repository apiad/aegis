"""What aegis promises about every harness, checked on the published release
of each against a fake model (conftest.py, fakemodel.py).

Each test scripts the fake, then drives a session the way a person or an agent
does, and reads the result where aegis keeps it: the transcript, the card, the
monitors. A scenario's marker word (``CANARY-...``) picks its script, so the
prompts aegis adds around a message cannot confuse it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ..conftest import until
from .fakemodel import Call, Rule, Say, Shell

pytestmark = pytest.mark.canary
TURN = 120  # seconds: a real harness starting cold, a fake model answering


def prose(s) -> list[str]:
    return [e.get("md") or "" for e in s.entries() if e["kind"] == "prose"]


def tools(s) -> list[dict]:
    return [e for e in s.entries() if e["kind"] == "tool"]


def said(s, text: str) -> bool:
    return any(text in p for p in prose(s))


async def test_a_turn_reaches_the_model_and_its_answer_reaches_the_transcript(
    canary,
):
    canary.model.script(Rule("CANARY-PING", [Say("CANARY-PONG")]))
    s = await canary.spawn()
    await canary.send(s, "CANARY-PING")
    await until(
        lambda: said(s, "CANARY-PONG") and s.status == "idle",
        timeout=TURN,
        what="the answer, and the turn's end",
    )


async def test_an_aegis_tool_is_offered_called_and_answered(canary):
    canary.model.script(Rule("CANARY-META", [Call("meta"), Say("CANARY-META-DONE")]))
    s = await canary.spawn()
    await canary.send(s, "CANARY-META")
    await until(
        lambda: said(s, "CANARY-META-DONE") and s.status == "idle",
        timeout=TURN,
        what="the turn after the meta call",
    )
    calls = [t for t in tools(s) if "meta" in (t.get("title") or "")]
    assert calls, (
        f"no meta call in the transcript: {[t.get('title') for t in tools(s)]}"
    )
    assert all(t["status"] == "ok" for t in calls), calls


async def test_turn_end_and_plan_update_reach_the_card(canary):
    canary.model.script(
        Rule(
            "CANARY-CARD",
            [
                Call(
                    "plan_update",
                    {"items": [{"text": "canary step", "state": "doing"}]},
                ),
                Call(
                    "turn_end",
                    {"attention": "review", "line": "canary line to read"},
                ),
                Say("CANARY-CARD-DONE"),
            ],
        )
    )
    s = await canary.spawn()
    await canary.send(s, "CANARY-CARD")
    await until(
        lambda: said(s, "CANARY-CARD-DONE") and s.status == "idle",
        timeout=TURN,
        what="the turn after turn_end",
    )
    w = s.wire()
    assert [i["text"] for i in w["plan"]] == ["canary step"], w["plan"]
    assert (
        w["attention"] == "review" and w["attention_line"] == "canary line to read"
    ), (
        w["attention"],
        w["attention_line"],
    )


async def test_a_monitor_armed_by_the_agent_wakes_it(canary):
    flag = canary.root / "canary.flag"
    canary.model.script(
        Rule("from monitor:", [Say("CANARY-WOKEN")]),
        Rule(
            "CANARY-MONITOR",
            [
                Call(
                    "monitor_start",
                    {
                        "description": "canary flag",
                        "done": f"test -f {flag}",
                        "progress": None,
                        "source": None,
                        "interval_s": 1,
                    },
                ),
                Say("CANARY-WAITING"),
            ],
        ),
    )
    s = await canary.spawn()
    await canary.send(s, "CANARY-MONITOR")
    await until(
        lambda: canary.app.monitors.of(s.log_id),
        timeout=TURN,
        what="the monitor the agent armed",
    )
    await until(lambda: s.status == "idle", timeout=TURN, what="the turn's end")
    flag.touch()
    await until(
        lambda: said(s, "CANARY-WOKEN") and s.status == "idle",
        timeout=TURN,
        what="the wake, and the answer to it",
    )


async def test_the_agents_shell_runs_a_command(canary):
    out = canary.root / "shell.out"
    canary.model.script(
        Rule("CANARY-SHELL", [Shell(f"echo canary > {out}"), Say("CANARY-RAN")])
    )
    s = await canary.spawn()
    await canary.send(s, "CANARY-SHELL")
    await until(
        lambda: said(s, "CANARY-RAN") and s.status == "idle",
        timeout=TURN,
        what="the turn after the shell call",
    )
    assert out.read_text().strip() == "canary"


async def test_an_interrupt_ends_a_running_turn(canary):
    canary.model.script(
        Rule("CANARY-SLOW", [Say("CANARY-TOO-LATE", delay=60)]),
        Rule("CANARY-AFTER", [Say("CANARY-STILL-HERE")]),
    )
    s = await canary.spawn()
    await canary.send(s, "CANARY-SLOW")
    await until(lambda: s.status == "working", timeout=TURN, what="the turn to start")
    await until(
        lambda: any("CANARY-SLOW" in x["last_person"] for x in canary.model.log),
        timeout=TURN,
        what="the request to reach the model",
    )
    await s.interrupt()
    await until(lambda: s.status == "idle", timeout=30, what="idle after interrupt")
    await canary.send(s, "CANARY-AFTER")
    await until(
        lambda: said(s, "CANARY-STILL-HERE") and s.status == "idle",
        timeout=TURN,
        what="a turn after the interrupt",
    )
    assert not said(s, "CANARY-TOO-LATE")


async def test_a_stopped_session_resumes_with_its_context(canary):
    canary.model.script(
        Rule("CANARY-REMEMBER", [Say("CANARY-NOTED")]),
        Rule("CANARY-RECALL", [Say("", recall="PELICAN")]),
    )
    s = await canary.spawn()
    await canary.send(s, "CANARY-REMEMBER the word PELICAN")
    await until(
        lambda: said(s, "CANARY-NOTED") and s.status == "idle",
        timeout=TURN,
        what="the first turn",
    )
    await s.stop()
    await canary.send(s, "CANARY-RECALL")
    await until(
        lambda: len(prose(s)) >= 2 and s.status == "idle",
        timeout=TURN,
        what="the resumed turn",
    )
    assert prose(s)[-1].strip() == "PELICAN", prose(s)


async def test_tutorial_spawns_a_guide_that_reads_its_page(canary):
    canary.model.script(
        Rule(
            "guide me through it",
            [
                Call("help_read", {"page": "getting-started"}),
                Call("turn_end", {"attention": "needs_you", "line": "canary step one"}),
                Say("Welcome to the aegis tutorial"),
            ],
        )
    )
    s = await canary.spawn()
    r = await canary.send(s, "/tutorial")
    guide = canary.app.sessions.sessions[r["log_id"]]
    await until(
        lambda: said(guide, "Welcome to the aegis tutorial") and guide.status == "idle",
        timeout=TURN,
        what="the guide's first turn",
    )
    reads = [t for t in tools(guide) if "help_read" in (t.get("title") or "")]
    assert reads and all(t["status"] == "ok" for t in reads), tools(guide)
    assert guide.wire()["attention"] == "needs_you"


def last_request(canary, marker: str) -> dict:
    return next(
        x for x in reversed(canary.model.log) if marker in x.get("last_person", "")
    )


async def test_an_agent_spawns_a_session_that_runs_its_prompt(canary):
    canary.model.script(
        Rule("CANARY-CHILD", [Say("CANARY-CHILD-DONE")]),
        Rule(
            "CANARY-PARENT",
            [
                Call("session_spawn", {"prompt": "CANARY-CHILD"}),
                Say("CANARY-SPAWNED"),
            ],
        ),
    )
    s = await canary.spawn()
    await canary.send(s, "CANARY-PARENT")
    await until(
        lambda: said(s, "CANARY-SPAWNED") and s.status == "idle",
        timeout=TURN,
        what="the parent's turn",
    )
    others = lambda: [x for x in canary.app.sessions.sessions.values() if x is not s]  # noqa: E731
    await until(
        lambda: any(said(x, "CANARY-CHILD-DONE") for x in others()),
        timeout=TURN,
        what="the child's answer",
    )


async def test_a_queue_worker_runs_and_its_result_wakes_the_enqueuer(canary):
    canary.model.script(
        Rule("from queue:", [Say("CANARY-GOT-RESULT")]),
        Rule("CANARY-WORK", [Say("WORKER-SAID-77")]),
        Rule(
            "CANARY-ENQUEUE",
            [
                Call("queue_enqueue", {"queue": "canary", "payload": "CANARY-WORK"}),
                Say("CANARY-ENQUEUED"),
            ],
        ),
    )
    s = await canary.spawn()
    await canary.send(s, "CANARY-ENQUEUE")
    await until(
        lambda: said(s, "CANARY-GOT-RESULT") and s.status == "idle",
        timeout=TURN * 2,
        what="the worker's result in the enqueuer's inbox, and the answer",
    )
    inbox = [e for e in s.entries() if e["kind"] == "inbox"]
    assert any("WORKER-SAID-77" in json.dumps(e) for e in inbox), inbox


async def test_a_handoff_reaches_the_target_and_peer_read_reads_it(canary):
    target = await canary.spawn()
    s = await canary.spawn()
    canary.model.script(
        Rule("CANARY-HANDED", [Say("CANARY-RECEIVED")]),
        Rule(
            "CANARY-HAND",
            [
                Call(
                    "peer_handoff",
                    {"target": target.handle, "context": "CANARY-HANDED"},
                ),
                Say("CANARY-HANDED-OFF"),
            ],
        ),
        Rule(
            "CANARY-READ",
            [Call("peer_read", {"target": target.handle}), Say("CANARY-READ-DONE")],
        ),
    )
    await canary.send(s, "CANARY-HAND")
    await until(
        lambda: said(target, "CANARY-RECEIVED") and target.status == "idle",
        timeout=TURN,
        what="the target's answer to the handoff",
    )
    await until(lambda: s.status == "idle", timeout=TURN, what="the sender idle")
    await canary.send(s, "CANARY-READ")
    await until(
        lambda: said(s, "CANARY-READ-DONE") and s.status == "idle",
        timeout=TURN,
        what="the turn after peer_read",
    )
    read = last_request(canary, "CANARY-READ")
    assert any("CANARY-RECEIVED" in r for r in read["tool_results"]), read


async def test_model_and_effort_change_mid_session(canary):
    canary.model.script(
        Rule("CANARY-ONE", [Say("CANARY-ONE-DONE")]),
        Rule("CANARY-TWO", [Say("CANARY-TWO-DONE")]),
    )
    s = await canary.spawn()
    await canary.send(s, "CANARY-ONE")
    await until(
        lambda: said(s, "CANARY-ONE-DONE") and s.status == "idle",
        timeout=TURN,
        what="the first turn",
    )
    before = last_request(canary, "CANARY-ONE")["model"]
    cat = await canary.app.catalogs.get(s)
    assert cat is not None, "no catalog to pick a model from"
    others = [m for m in cat.models if m.value != s.spec.model]
    assert others, [m.value for m in cat.models]
    # The fake's second model where the harness lists it; else its first other.
    target = next((m for m in others if m.value.endswith("-two")), others[0])
    await canary.send(s, f"/model {target.value}")
    from aegis.app import EFFORTS

    usable = [e for e in target.efforts if e in EFFORTS]
    if usable:
        await canary.send(s, f"/effort {usable[-1]}")
    await canary.send(s, "CANARY-TWO")
    await until(
        lambda: said(s, "CANARY-TWO-DONE") and s.status == "idle",
        timeout=TURN,
        what="the turn on the new model",
    )
    after = last_request(canary, "CANARY-TWO")["model"]
    assert after != before, f"/model {target.value} still sent {after!r}"


async def test_a_read_session_calls_aegis_but_cannot_write(canary):
    out = canary.root / "read.out"
    canary.model.script(
        Rule(
            "CANARY-READONLY",
            [
                Call("turn_end", {"attention": "review", "line": "canary read"}),
                Shell(f"echo nope > {out}"),
                Say("CANARY-READ-ENDED"),
            ],
        )
    )
    s = await canary.spawn(permission="read")
    await canary.send(s, "CANARY-READONLY")
    await until(
        lambda: said(s, "CANARY-READ-ENDED") and s.status == "idle",
        timeout=TURN,
        what="the read session's turn",
    )
    ends = [t for t in tools(s) if "turn_end" in (t.get("title") or "")]
    refused = [t["detail"].get("result") for t in ends if t["status"] != "ok"]
    assert ends and not refused, f"a read session's turn_end was refused: {refused}"
    assert s.wire()["attention_line"] == "canary read"
    assert not out.exists(), "a read session wrote a file through its shell"


async def test_a_permission_change_applies_to_the_next_turn(canary):
    out = canary.root / "perm.out"
    canary.model.script(
        Rule("CANARY-FIRST", [Say("CANARY-FIRST-DONE")]),
        Rule(
            "CANARY-LOCKED",
            [Shell(f"echo nope > {out}"), Say("CANARY-LOCKED-DONE")],
        ),
    )
    s = await canary.spawn()
    await canary.send(s, "CANARY-FIRST")
    await until(
        lambda: said(s, "CANARY-FIRST-DONE") and s.status == "idle",
        timeout=TURN,
        what="the first turn",
    )
    await canary.send(s, "/permission read")
    await canary.send(s, "CANARY-LOCKED")
    await until(
        lambda: said(s, "CANARY-LOCKED-DONE") and s.status == "idle",
        timeout=TURN,
        what="the turn after /permission read",
    )
    assert not out.exists(), "after /permission read the shell still wrote a file"


async def test_a_message_sent_mid_turn_is_answered(canary):
    canary.model.script(
        Rule("CANARY-BUSY", [Say("CANARY-BUSY-DONE", delay=4)]),
        Rule("CANARY-MIDTURN", [Say("CANARY-MIDTURN-DONE")]),
    )
    s = await canary.spawn()
    await canary.send(s, "CANARY-BUSY")
    await until(
        lambda: any("CANARY-BUSY" in x["last_person"] for x in canary.model.log),
        timeout=TURN,
        what="the first request",
    )
    await canary.send(s, "CANARY-MIDTURN")
    await until(
        lambda: said(s, "CANARY-MIDTURN-DONE") and s.status == "idle",
        timeout=TURN,
        what="the answer to the message sent mid-turn",
    )


async def test_the_catalog_lists_the_sessions_model_and_commands(canary):
    canary.model.script(Rule("CANARY-CAT", [Say("CANARY-CAT-DONE")]))
    s = await canary.spawn()
    await canary.send(s, "CANARY-CAT")
    await until(lambda: said(s, "CANARY-CAT-DONE"), timeout=TURN, what="a turn")
    cat = await canary.app.catalogs.get(s)
    assert cat is not None, "no catalog"
    values = [m.value for m in cat.models]
    assert s.spec.model in values, values


async def test_an_artifact_is_created(canary):
    canary.model.script(
        Rule(
            "CANARY-ART",
            [Call("artifact_create", {"title": "Canary page"}), Say("CANARY-ART-DONE")],
        )
    )
    s = await canary.spawn()
    await canary.send(s, "CANARY-ART")
    await until(
        lambda: said(s, "CANARY-ART-DONE") and s.status == "idle",
        timeout=TURN,
        what="the turn after artifact_create",
    )
    made = [t for t in tools(s) if "artifact_create" in (t.get("title") or "")]
    assert made and all(t["status"] == "ok" for t in made), tools(s)


async def test_a_session_resumes_its_context_after_the_server_restarts(canary):
    canary.model.script(
        Rule("CANARY-REMEMBER", [Say("CANARY-NOTED")]),
        Rule("CANARY-RECALL", [Say("", recall="PELICAN")]),
    )
    s = await canary.spawn()
    await canary.send(s, "CANARY-REMEMBER the word PELICAN")
    await until(
        lambda: said(s, "CANARY-NOTED") and s.status == "idle",
        timeout=TURN,
        what="the first turn",
    )
    await canary.restart()
    s = canary.app.sessions.sessions[s.log_id]
    await canary.send(s, "CANARY-RECALL")
    await until(
        lambda: len(prose(s)) >= 2 and s.status == "idle",
        timeout=TURN,
        what="the turn after the restart",
    )
    assert prose(s)[-1].strip() == "PELICAN", prose(s)


async def test_an_attached_file_reaches_the_agent_and_it_can_read_it(canary):
    out = canary.root / "attach.out"
    canary.model.script(
        Rule(
            "CANARY-ATTACH",
            [Shell(f"cat {{path:note.txt}} > {out}"), Say("CANARY-ATTACH-DONE")],
        )
    )
    s = await canary.spawn()
    uid = await canary.upload(s, "note.txt", b"CANARY-SECRET-42\n")
    await canary.send(s, "CANARY-ATTACH", [uid])
    await until(
        lambda: said(s, "CANARY-ATTACH-DONE") and s.status == "idle",
        timeout=TURN,
        what="the turn with the attachment",
    )
    assert out.exists() and "CANARY-SECRET-42" in out.read_text(), last_request(
        canary, "CANARY-ATTACH"
    )["last_person"]


async def test_the_harness_own_compact_command_runs(canary):
    if canary.harness == "opencode":
        # OpenCode 2's server lists no compact command (2.0.31): it compacts
        # on its own when the context fills, and its TUI's /compact is the
        # TUI's.
        pytest.skip("OpenCode 2 has no /compact command to pass through")
    canary.model.script(
        Rule("CANARY-BEFORE", [Say("CANARY-BEFORE-DONE")]),
        Rule("CANARY-AFTER-COMPACT", [Say("CANARY-AFTER-COMPACT-DONE")]),
    )
    s = await canary.spawn()
    await canary.send(s, "CANARY-BEFORE")
    await until(
        lambda: said(s, "CANARY-BEFORE-DONE") and s.status == "idle",
        timeout=TURN,
        what="the first turn",
    )
    cat = await canary.app.catalogs.get(s)
    assert cat is not None and cat.has("compact"), "the catalog has no /compact"
    await canary.send(s, "/compact")
    await until(lambda: s.status == "idle", timeout=TURN, what="the compaction")
    await canary.send(s, "CANARY-AFTER-COMPACT")
    await until(
        lambda: said(s, "CANARY-AFTER-COMPACT-DONE") and s.status == "idle",
        timeout=TURN,
        what="a turn after /compact",
    )


async def test_close_ends_the_harness_process(canary):
    canary.model.script(Rule("CANARY-BYE", [Say("CANARY-BYE-DONE")]))
    s = await canary.spawn()
    await canary.send(s, "CANARY-BYE")
    await until(lambda: said(s, "CANARY-BYE-DONE"), timeout=TURN, what="a turn")
    pid = s.wire().get("pid")
    assert pid, s.wire()
    await canary.send(s, "/close")
    await until(lambda: not _alive(pid), timeout=30, what="the harness to exit")


def _alive(pid: int) -> bool:
    import os

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split()[2] != "Z"
    except OSError:
        return False


async def test_the_opencode_the_official_installer_gives_is_refused_in_words(
    tmp_path, monkeypatch, request
):
    """opencode.ai/install and the ``opencode-ai`` package install OpenCode 1,
    which aegis does not drive. A person who installed it the documented way
    must read what to do, not a traceback. Runs once, not per setup."""
    import os
    from pathlib import Path

    from aegis.app import App
    from aegis.roots import make_roots

    folder = os.environ.get("AEGIS_CANARY_OPENCODE1")
    if not folder:
        pytest.skip("AEGIS_CANARY_OPENCODE1 names no OpenCode 1 install")
    bin = str(Path(folder) / "opencode")
    import subprocess

    version = subprocess.run(
        [bin, "--version"], capture_output=True, text=True, timeout=60
    ).stdout.strip()
    request.node.user_properties += [
        ("harness", "opencode"),
        ("setup", "opencode-1"),
        ("version", f"opencode {version}"),
    ]
    root = tmp_path / "root"
    root.mkdir()
    (root / ".aegis.yaml").write_text(
        "agents:\n  oc1: {harness: opencode, model: opencode/big-pickle, "
        "effort: low, permission: full}\n"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    app = App(make_roots(root, None), opencode_bin=bin)
    read: list[str] = []
    try:
        try:
            r = await app.registry.call("session.spawn", {"agent": "oc1"})
            s = app.sessions.sessions[r["log_id"]]
            await app.registry.call("session.send", {"log_id": s.log_id, "text": "hi"})
            await until(lambda: s.status != "working", timeout=30, what="the turn")
            read += [
                e.get("summary") or "" for e in s.entries() if e["kind"] == "error"
            ]
        except Exception as e:  # what the browser shows for a failed op
            read.append(f"{type(e).__name__}: {e}")
        assert any("@opencode/cli" in t for t in read), read
    finally:
        for s in list(app.sessions.sessions.values()):
            await s.stop()


async def test_a_rate_limited_model_falls_to_the_next_in_its_cascade(canary):
    """The real harness's words for a 429 are ones cascade.classify falls on,
    and the session continues the turn on the second model."""
    if canary.harness == "codex":
        pytest.skip("no Codex error falls yet (cascade.py)")
    canary.model.script(
        Rule("CANARY-429", [Say("CANARY-429-UNREACHED")]),
        Rule("You are now running on", [Say("CANARY-429-RECOVERED")]),
    )
    s = await canary.spawn(agent="cascade")
    first = s.spec.model
    await canary.send(s, "CANARY-429")
    await until(
        lambda: said(s, "CANARY-429-RECOVERED") and s.status == "idle",
        timeout=TURN,
        what="the turn to fall to the second model and finish",
    )
    rows = [e["summary"] for e in s.entries() if e["kind"] == "system"]
    assert any(r.startswith(f"cascade: {first} failed") for r in rows), rows
    assert s.spec.model == f"{first}-two"
    assert not said(s, "CANARY-429-UNREACHED")


async def test_a_notice_reaches_the_model_on_a_prompt_and_on_a_tool_result(canary):
    # Agent status (#522) rides only on what aegis writes: the prompt, and
    # either a steer at a tool call or its own tool results. This is the check
    # that every harness hands both on.
    from aegis.notices import Item

    canary.model.script(
        Rule(
            "CANARY-NOTE",
            [
                Shell("sleep 3"),
                Call("plan_update", {"items": [{"text": "a", "state": "doing"}]}),
                Say("CANARY-NOTE-DONE"),
            ],
        )
    )
    s = await canary.spawn()
    n = canary.app.notices
    on_prompt = Item("p:on", "warn", "seen on the prompt: alpha-7")
    n.set(s.log_id, "monitors", [on_prompt])
    await canary.send(s, "CANARY-NOTE")
    await until(
        lambda: any("CANARY-NOTE" in x["last_person"] for x in canary.model.log),
        timeout=TURN,
        what="the first request",
    )
    # While the shell sleeps, before the aegis call: owed on its result.
    n.set(
        s.log_id,
        "monitors",
        [on_prompt, Item("t:on", "warn", "on a tool result: bravo-9")],
    )
    await until(
        lambda: said(s, "CANARY-NOTE-DONE") and s.status == "idle",
        timeout=TURN,
        what="the turn after plan_update",
    )
    log = canary.model.log
    assert any("alpha-7" in x["last_person"] for x in log), "no prompt carried it"
    if s.harness.steers_notices:
        assert any("bravo-9" in x["whole"] for x in log), "no steer carried it"
        replies = [x for x in log if "CANARY-NOTE-DONE" in str(x.get("reply"))]
        assert len(replies) == 1, "a steered notice started a turn of its own"
    else:
        assert any("bravo-9" in r for x in log for r in x["tool_results"]), (
            f"no tool result carried it: {[x['tool_results'] for x in log]}"
        )


async def test_knowledge_native_starts_claude_without_its_skills_and_memory(canary):
    """The person's knowledge switch (knowledge.setting): off, Claude Code
    offers its Skill tool and its auto-memory; on, a new session has
    neither. Pinned on Claude Code, whose switches were read from its
    release; Codex's and OpenCode's are config keys (codex/config.py,
    opencode/config.py)."""
    if canary.harness != "claude-code":
        pytest.skip("the switch is pinned on Claude Code")
    # A skill of the harness's own, so its Skill tool has one to offer.
    own = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "skills" / "canary-own"
    own.mkdir(parents=True)
    (own / "SKILL.md").write_text(
        "---\nname: canary-own\ndescription: Use for the canary\n---\nSay hi.\n"
    )
    canary.model.script(Rule("CANARY-NATIVE", [Say("CANARY-NATIVE-DONE")]))

    async def ask(marker: str) -> dict:
        s = await canary.spawn()
        await canary.send(s, marker)
        await until(
            lambda: said(s, "CANARY-NATIVE-DONE") and s.status == "idle",
            timeout=TURN,
            what="the turn",
        )
        return last_request(canary, marker)

    memory = "persistent file-based memory"  # its auto-memory section
    before = await ask("CANARY-NATIVE-OFF")
    system = canary.model.systems[before["system"]]
    assert "Skill" in before["tools"], before["tools"]
    assert memory in system and "canary-own" in system
    await canary.app.registry.call("knowledge.setting", {"native": True})
    after = await ask("CANARY-NATIVE-ON")
    system = canary.model.systems[after["system"]]
    assert "Skill" not in after["tools"], after["tools"]
    assert memory not in system and "canary-own" not in system
    assert "mcp__aegis__skill_read" in after["tools"] or any(
        t in after["tools"] for t in ("ToolSearch", "tool_search")
    ), after["tools"]
