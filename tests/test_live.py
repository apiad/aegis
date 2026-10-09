"""One real Claude Code session through aegis: a prompt, an interrupt, a stop
and a resume that keeps the context.

Spends a few cents of Haiku. Run with ``make test-live``."""

import json
import shutil
from pathlib import Path

import pytest

from aegis.meta import MetaStore
from aegis.session import Session, SpawnSpec
from aegis.transcript.entries import fold_records
from aegis.transcript.store import Store, read_store

from .conftest import until

pytestmark = pytest.mark.live
HAIKU = "claude-haiku-4-5-20251001"
# Haiku at low effort followed the primer's Bash paragraph in 2 of 3 runs, so
# the test of that paragraph runs on Sonnet, which followed it in 3 of 3.
SONNET = "claude-sonnet-5"


async def test_a_real_prompt_interrupt_stop_and_resume(tmp_path: Path):
    claude = shutil.which("claude")
    assert claude, "claude is not on PATH"
    path = tmp_path / "log.jsonl"
    s = Session(
        log_id="live",
        spec=SpawnSpec("haiku", HAIKU, "low", "full", tmp_path),
        handle="live-test",
        store=Store(path),
        stderr_path=tmp_path / "stderr.log",
        claude_bin=claude,
        publish=lambda ch, ops: None,
        metas=MetaStore(tmp_path / "sessions"),
    )
    await s.start()
    try:
        await s.send("Remember the word PELICAN. Reply with the single word OK.")
        await until(
            lambda: s.status == "idle" and s.cost_usd,
            timeout=90,
            what="the first result",
        )
        assert s.context_window and s.context_tokens and s.resume_id

        await s.send(
            "Run exactly this bash command in the foreground: python3 -c 'import time; time.sleep(40)'"
        )
        await until(
            lambda: any(e["kind"] == "tool" for e in s.entries()),
            timeout=90,
            what="the Bash call",
        )
        await s.interrupt()
        await until(
            lambda: s.status == "idle", timeout=20, what="idle after the interrupt"
        )
        assert s.entries()[-1]["summary"].startswith("interrupted")

        await s.stop()
        await s.send("What word did I ask you to remember? Reply with just the word.")
        await until(lambda: s.status == "idle", timeout=90, what="the resumed turn")
        prose = [e["md"] for e in s.entries() if e["kind"] == "prose"]
        assert "PELICAN" in prose[-1]
    finally:
        await s.stop()
    records, damaged = read_store(path)
    assert damaged == 0 and fold_records(records).entries() == s.entries()


async def test_real_claude_arms_a_monitor_through_the_endpoint_and_is_woken(
    tmp_path: Path,
):
    """The real binary against the real /mcp: tool listing, the token header,
    the primer, and the inbox wake."""
    import asyncio

    import uvicorn

    from aegis.app import App
    from aegis.roots import make_roots
    from aegis.web import build_web

    from .test_agents import _free_port

    claude = shutil.which("claude")
    assert claude, "claude is not on PATH"
    (tmp_path / ".aegis.yaml").write_text(
        f"agents:\n  haiku: {{harness: claude-code, model: {HAIKU}, effort: low, permission: full}}\n"
    )
    port = _free_port()
    app = App(
        make_roots(tmp_path, None),
        claude_bin=claude,
        base_url=f"http://127.0.0.1:{port}",
    )
    server = uvicorn.Server(
        uvicorn.Config(
            build_web(app, "t", {f"127.0.0.1:{port}"}), port=port, log_level="warning"
        )
    )
    task = asyncio.create_task(server.serve())
    await until(lambda: server.started, timeout=10, what="uvicorn")
    try:
        r = await app.registry.call("session.spawn", {"agent": "haiku"})
        s = app.sessions.sessions[r["log_id"]]
        flag = tmp_path / "ready.flag"
        await s.send(
            f"Use the aegis monitor_start tool to wait until the file {flag} exists "
            "(done condition `test -f <that path>`, interval 2 seconds). Then end your turn. "
            "When you are woken, reply with the single word WOKEN."
        )
        await until(
            lambda: app.monitors.of(s.log_id),
            timeout=120,
            what="the monitor armed by Claude",
        )
        await until(
            lambda: s.status == "idle", timeout=60, what="Claude ending its turn"
        )
        flag.touch()
        await until(
            lambda: (
                any(e["kind"] == "inbox" for e in s.entries())
                and s.status == "idle"
                and any(
                    "WOKEN" in (e.get("md") or "")
                    for e in s.entries()
                    if e["kind"] == "prose"
                )
            ),
            timeout=120,
            what="the wake and Claude's answer",
        )
        calls = [e["title"] for e in s.entries() if e["kind"] == "tool"]
        assert "monitor_start" in calls
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 30)


async def test_real_claude_sends_a_file_and_the_link_serves_it(tmp_path: Path):
    """The real binary finds file_send from its description and hands over a
    file it wrote; the transcript entry's link serves the same bytes."""
    import asyncio

    import httpx
    import uvicorn

    from aegis.app import App
    from aegis.roots import make_roots
    from aegis.web import build_web

    from .test_agents import _free_port

    claude = shutil.which("claude")
    assert claude, "claude is not on PATH"
    (tmp_path / ".aegis.yaml").write_text(
        f"agents:\n  haiku: {{harness: claude-code, model: {HAIKU}, effort: low, permission: full}}\n"
    )
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    app = App(make_roots(tmp_path, None), claude_bin=claude, base_url=base)
    server = uvicorn.Server(
        uvicorn.Config(
            build_web(app, "t", {f"127.0.0.1:{port}"}), port=port, log_level="warning"
        )
    )
    task = asyncio.create_task(server.serve())
    await until(lambda: server.started, timeout=10, what="uvicorn")
    try:
        r = await app.registry.call("session.spawn", {"agent": "haiku"})
        s = app.sessions.sessions[r["log_id"]]
        await s.send(
            "Write a file named haiku.md in your working directory holding a "
            "three-line poem about pelicans, then hand it to me with the aegis "
            "tool for sending files, captioned 'A pelican haiku'. Then end your turn."
        )
        await until(
            lambda: (
                s.status == "idle" and any(e["kind"] == "file" for e in s.entries())
            ),
            timeout=180,
            what="the file sent by Claude",
        )
        (e,) = [e for e in s.entries() if e["kind"] == "file"]
        assert e["title"] == "haiku.md" and e["detail"]["preview"] == "markdown"
        async with httpx.AsyncClient() as c:
            got = await c.get(base + e["detail"]["url"])
        assert got.content == (tmp_path / "haiku.md").read_bytes()
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 30)


async def test_real_claude_lands_an_artifact_and_hears_the_pick(tmp_path: Path):
    """The real binary finds artifact_create and artifact_send from their
    descriptions, lands a page with two choices, and answers the pick the
    person makes through the page."""
    import asyncio

    import uvicorn

    from aegis.app import App
    from aegis.roots import make_roots
    from aegis.web import build_web

    from .test_agents import _free_port

    claude = shutil.which("claude")
    assert claude, "claude is not on PATH"
    (tmp_path / ".aegis.yaml").write_text(
        f"agents:\n  haiku: {{harness: claude-code, model: {HAIKU}, effort: low, permission: full}}\n"
    )
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    app = App(make_roots(tmp_path, None), claude_bin=claude, base_url=base)
    server = uvicorn.Server(
        uvicorn.Config(
            build_web(app, "t", {f"127.0.0.1:{port}"}), port=port, log_level="warning"
        )
    )
    task = asyncio.create_task(server.serve())
    await until(lambda: server.started, timeout=10, what="uvicorn")
    try:
        r = await app.registry.call("session.spawn", {"agent": "haiku"})
        s = app.sessions.sessions[r["log_id"]]
        await s.send(
            "Offer me two layouts, A and B, as an aegis artifact with one button "
            "each, captioned 'Pick a layout'. Then end your turn and wait for my pick."
        )
        await until(
            lambda: (
                s.status == "idle" and any(e["kind"] == "artifact" for e in s.entries())
            ),
            timeout=180,
            what="the artifact",
        )
        (art,) = [e for e in s.entries() if e["kind"] == "artifact"]
        assert art["status"] == "live"
        await app.registry.call(
            "artifact.submit",
            {
                "log_id": s.log_id,
                "artifact_id": art["id"],
                "data": {"layout": "B"},
                "label": "Picked B",
            },
        )
        await until(
            lambda: (
                s.status == "idle"
                and "B"
                in ([e["md"] for e in s.entries() if e["kind"] == "prose"] or [""])[-1]
            ),
            timeout=120,
            what="the answer",
        )
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 30)


async def test_real_claude_runs_the_doctor_and_names_what_is_wrong(tmp_path: Path):
    """The real binary finds config_doctor from its description, and its
    findings are enough to name the key aegis does not read."""
    import asyncio

    import uvicorn

    from aegis.app import App
    from aegis.roots import make_roots
    from aegis.web import build_web

    from .test_agents import _free_port

    claude = shutil.which("claude")
    assert claude, "claude is not on PATH"
    (tmp_path / ".aegis.yaml").write_text(
        "scheduler: {tick_seconds: 5}\ndefault_agent: haiku\n"
        f"agents:\n  haiku: {{harness: claude-code, model: {HAIKU}, effort: low, permission: full}}\n"
    )
    port = _free_port()
    app = App(
        make_roots(tmp_path, None),
        claude_bin=claude,
        base_url=f"http://127.0.0.1:{port}",
    )
    server = uvicorn.Server(
        uvicorn.Config(
            build_web(app, "t", {f"127.0.0.1:{port}"}), port=port, log_level="warning"
        )
    )
    task = asyncio.create_task(server.serve())
    await until(lambda: server.started, timeout=10, what="uvicorn")
    try:
        r = await app.registry.call("session.spawn", {"agent": "haiku"})
        s = app.sessions.sessions[r["log_id"]]
        await s.send(
            "Check this workspace's aegis configuration with the aegis tool for "
            "that, and tell me in one line which top-level key in it aegis does "
            "not read. Then end your turn."
        )
        await until(
            lambda: s.status == "idle" and "config_doctor" in json.dumps(s.entries()),
            timeout=180,
            what="the doctor called by Claude",
        )
        await until(lambda: s.status == "idle", timeout=120, what="the answer")
        prose = [e["md"] for e in s.entries() if e["kind"] == "prose"]
        assert "scheduler" in prose[-1]
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 30)


async def test_real_claude_spawns_a_peer_through_session_spawn(tmp_path: Path):
    """The real binary finds session_spawn from its description and the new
    session runs its prompt."""
    import asyncio

    import uvicorn

    from aegis.app import App
    from aegis.roots import make_roots
    from aegis.web import build_web

    from .test_agents import _free_port

    claude = shutil.which("claude")
    assert claude, "claude is not on PATH"
    (tmp_path / ".aegis.yaml").write_text(
        f"agents:\n  haiku: {{harness: claude-code, model: {HAIKU}, effort: low, permission: full}}\n"
    )
    port = _free_port()
    app = App(
        make_roots(tmp_path, None),
        claude_bin=claude,
        base_url=f"http://127.0.0.1:{port}",
    )
    server = uvicorn.Server(
        uvicorn.Config(
            build_web(app, "t", {f"127.0.0.1:{port}"}), port=port, log_level="warning"
        )
    )
    task = asyncio.create_task(server.serve())
    await until(lambda: server.started, timeout=10, what="uvicorn")
    try:
        r = await app.registry.call("session.spawn", {"agent": "haiku"})
        s = app.sessions.sessions[r["log_id"]]
        await s.send(
            "Use the aegis session_spawn tool with agent haiku and prompt "
            "'Reply with the single word PONG.' Then reply with the single word DONE."
        )

        def child():
            return next(
                (
                    x
                    for x in app.sessions.sessions.values()
                    if x.spec.spawned_by == s.log_id
                ),
                None,
            )

        await until(
            lambda: child() is not None, timeout=120, what="the spawned session"
        )
        await until(
            lambda: any(
                "PONG" in (e.get("md") or "")
                for e in child().entries()
                if e["kind"] == "prose"
            ),
            timeout=120,
            what="the spawned session's answer",
        )
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 30)


async def test_real_claude_switches_model_and_effort_and_keeps_them_across_resume(
    tmp_path: Path,
):
    claude = shutil.which("claude")
    assert claude, "claude is not on PATH"
    path = tmp_path / "log.jsonl"
    s = Session(
        log_id="live-cmd",
        spec=SpawnSpec("haiku", HAIKU, "low", "full", tmp_path),
        handle="live-cmd",
        store=Store(path),
        stderr_path=tmp_path / "stderr.log",
        claude_bin=claude,
        publish=lambda ch, ops: None,
        metas=MetaStore(tmp_path / "sessions"),
    )
    await s.start()
    try:
        cat = await s.catalog_task
        assert cat and cat.has("compact") and cat.model("sonnet")
        await s.configure(model="sonnet", effort="low")
        await s.send("Reply with the single word OK.")
        await until(
            lambda: s.status == "idle" and s.cost_usd, timeout=90, what="the turn"
        )
        assert s.model_id and "sonnet" in s.model_id
        await s.send("/context")
        await until(
            lambda: (
                s.status == "idle" and any(e["kind"] == "command" for e in s.entries())
            ),
            timeout=60,
            what="/context",
        )
        await s.stop()
        await s.send("Reply with the single word OK.")
        await until(lambda: s.status == "idle", timeout=90, what="the resumed turn")
        assert "sonnet" in (s.model_id or "")
        assert not [e for e in s.entries() if e["status"] == "pending"]
    finally:
        await s.stop()
    records, damaged = read_store(path)
    assert damaged == 0 and fold_records(records).entries() == s.entries()


async def test_real_claude_gives_a_countable_wait_a_progress_command(tmp_path: Path):
    """Told only what to wait for, the real binary arms the monitor with a
    `progress` command because the tool and the primer ask for one (#165)."""
    import asyncio

    import uvicorn

    from aegis.app import App
    from aegis.roots import make_roots
    from aegis.web import build_web

    from .test_agents import _free_port

    claude = shutil.which("claude")
    assert claude, "claude is not on PATH"
    (tmp_path / ".aegis.yaml").write_text(
        f"agents:\n  haiku: {{harness: claude-code, model: {HAIKU}, effort: low, permission: full}}\n"
    )
    port = _free_port()
    app = App(
        make_roots(tmp_path, None),
        claude_bin=claude,
        base_url=f"http://127.0.0.1:{port}",
    )
    server = uvicorn.Server(
        uvicorn.Config(
            build_web(app, "t", {f"127.0.0.1:{port}"}), port=port, log_level="warning"
        )
    )
    task = asyncio.create_task(server.serve())
    await until(lambda: server.started, timeout=10, what="uvicorn")
    try:
        r = await app.registry.call("session.spawn", {"agent": "haiku"})
        s = app.sessions.sessions[r["log_id"]]
        # A CI wait: 33 of the 44 monitors armed without progress in early
        # October waited on GitHub checks or runs, all of them countable. Before
        # #165 this prompt got a progress command in 1 of 6 runs.
        await s.send(
            "CI is running on pull request 12 of the GitHub repo apiad/aegis. Wait "
            "for its checks to finish with the aegis monitor_start tool, then end "
            "your turn. Do not run any command yourself first."
        )
        await until(
            lambda: app.monitors.of(s.log_id),
            timeout=120,
            what="the monitor armed by Claude",
        )
        (m,) = app.monitors.of(s.log_id)
        assert m.progress, f"armed with no progress command: {m}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 30)


def _bash_commands(path: Path) -> list[str]:
    """Every Bash command a store recorded, as Claude wrote it."""
    found = []

    def walk(o):
        if isinstance(o, dict):
            if o.get("type") == "tool_use" and o.get("name") == "Bash":
                found.append(o["input"]["command"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    for r in read_store(path)[0]:
        if r.get("src") == "claude":
            walk(json.loads(r["line"]))
    return found


async def test_real_claude_names_a_bash_call_and_ends_it_on_a_counted_verdict(
    tmp_path: Path,
):
    """The primer's Bash paragraphs reach the real binary: a search opens on a
    comment that names it, and its plain answer, which would end on a file
    name, ends on the count it found."""
    import asyncio
    import re

    import uvicorn

    from aegis.app import App
    from aegis.roots import make_roots
    from aegis.web import build_web

    from .test_agents import _free_port

    claude = shutil.which("claude")
    assert claude, "claude is not on PATH"
    (tmp_path / ".aegis.yaml").write_text(
        f"agents:\n  sonnet: {{harness: claude-code, model: {SONNET}, effort: low, permission: full}}\n"
    )
    logs = tmp_path / "logs"
    logs.mkdir()
    names = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf"]
    for k, name in enumerate(names):
        (logs / f"{name}.log").write_text("ERROR disk full\n" if k % 3 == 0 else "ok\n")
    hits = sum(k % 3 == 0 for k in range(len(names)))
    port = _free_port()
    app = App(
        make_roots(tmp_path, None),
        claude_bin=claude,
        base_url=f"http://127.0.0.1:{port}",
    )
    server = uvicorn.Server(
        uvicorn.Config(
            build_web(app, "t", {f"127.0.0.1:{port}"}), port=port, log_level="warning"
        )
    )
    task = asyncio.create_task(server.serve())
    await until(lambda: server.started, timeout=10, what="uvicorn")
    try:
        r = await app.registry.call("session.spawn", {"agent": "sonnet"})
        s = app.sessions.sessions[r["log_id"]]
        await s.send(
            f"With one Bash call, list the files in {logs} that contain the word "
            "ERROR. Then end your turn."
        )
        await until(lambda: s.status == "idle", timeout=180, what="the Bash turn")
        rows = [e for e in s.entries() if e["kind"] == "tool" and e["title"] == "Bash"]
        assert rows, "Claude made no Bash call"
        verdict = rows[-1]["detail"]["result"]
        assert re.search(rf"\b{hits}\b", verdict), verdict
        (command, *_) = _bash_commands(s.store.path)
        assert command.lstrip().startswith("#"), command
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 30)


async def test_real_claude_reports_its_turns_with_turn_end(tmp_path: Path):
    """A real Sonnet primed by aegis calls turn_end: needs_you with replies after
    laying out two options, done without needs_you after finished work. Sonnet,
    not Haiku: Haiku called turn_end in about 1 of 3 runs whatever the primer's
    wording (#171)."""
    import asyncio

    import uvicorn

    from aegis.app import App
    from aegis.roots import make_roots
    from aegis.web import build_web

    from .test_agents import _free_port

    claude = shutil.which("claude")
    assert claude, "claude is not on PATH"
    (tmp_path / ".aegis.yaml").write_text(
        f"agents:\n  sonnet: {{harness: claude-code, model: {SONNET}, effort: low, permission: full}}\n"
    )
    port = _free_port()
    app = App(
        make_roots(tmp_path, None),
        claude_bin=claude,
        base_url=f"http://127.0.0.1:{port}",
    )
    server = uvicorn.Server(
        uvicorn.Config(
            build_web(app, "t", {f"127.0.0.1:{port}"}), port=port, log_level="warning"
        )
    )
    task = asyncio.create_task(server.serve())
    await until(lambda: server.started, timeout=10, what="uvicorn")
    try:
        r = await app.registry.call("session.spawn", {"agent": "sonnet"})
        s = app.sessions.sessions[r["log_id"]]
        await s.send(
            "I need to bring a feature branch up to date with main. Lay out the two "
            "usual ways in two lines and ask me which one I want. Do not run anything."
        )
        await until(
            lambda: s.status == "idle" and s.cost_usd,
            timeout=120,
            what="the question turn",
        )
        c = s.wire()
        assert c["attention"] == "needs_you", c
        assert 1 <= len(c["replies"]) <= 3, c
        await s.send(
            f"Create the file {tmp_path / 'done.txt'} containing ok, then tell me it is done."
        )
        await until(
            lambda: s.status == "idle" and (tmp_path / "done.txt").exists(),
            timeout=120,
            what="the work turn",
        )
        assert (s.standing.get("report") or {}).get("attention") == "done", s.standing
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 30)


async def test_a_real_haiku_recap_of_a_spanish_session_is_in_spanish(
    tmp_path: Path,
):
    """A real Sonnet session answers two Spanish prompts; a forced recap runs a
    real Haiku over it, comes back in Spanish, and its cost lands on the record
    and on the card (#171)."""
    import asyncio

    import uvicorn

    from aegis.app import App
    from aegis.roots import make_roots
    from aegis.web import build_web

    from .test_agents import _free_port

    claude = shutil.which("claude")
    assert claude, "claude is not on PATH"
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n"
        f"  sonnet: {{harness: claude-code, model: {SONNET}, effort: low, permission: full}}\n"
        f"  haiku: {{harness: claude-code, model: {HAIKU}, effort: low, permission: full}}\n"
        "recap: {agent: haiku}\n"
    )
    port = _free_port()
    app = App(
        make_roots(tmp_path, None),
        claude_bin=claude,
        base_url=f"http://127.0.0.1:{port}",
    )
    server = uvicorn.Server(
        uvicorn.Config(
            build_web(app, "t", {f"127.0.0.1:{port}"}), port=port, log_level="warning"
        )
    )
    task = asyncio.create_task(server.serve())
    await until(lambda: server.started, timeout=10, what="uvicorn")
    try:
        r = await app.registry.call("session.spawn", {"agent": "sonnet"})
        s = app.sessions.sessions[r["log_id"]]
        for n, prompt in enumerate(
            ("Explícame en dos frases qué es un rebase.", "¿Y un merge?"), 1
        ):
            await s.send(prompt)
            await until(
                lambda n=n: (
                    s.status == "idle"
                    and sum(e["kind"] == "user" for e in s.entries()) == n
                    and s.entries()[-1]["kind"] != "user"
                ),
                timeout=120,
                what=f"answer {n}",
            )
        out = await app.registry.call(
            "recap.request", {"log_id": s.log_id, "force": True}
        )
        assert out["status"] == "made", out
        (rec,) = [e for e in s.entries() if e["kind"] == "recap"]
        context = rec["detail"]["context"]
        print(f"\nrecap context: {context}\nrecap ask: {rec['detail']['ask']}")
        print(f"recap cost: {rec['detail']['cost_usd']}")
        assert context.strip(), rec
        # A Spanish sentence can miss any short list of articles ("Explicar las
        # diferencias entre rebase y merge en Git." in one run), so the list is
        # wide, and an English stopword rules out an English answer.
        words = f" {context.lower().rstrip('.')} "
        spanish = (" el ", " la ", " los ", " las ", " de ", " del ", " que ")
        spanish += (" en ", " y ", " entre ", " para ", " con ", " un ", " una ")
        assert any(w in words for w in spanish), context
        assert not any(w in words for w in (" the ", " and ", " of ", " to ")), context
        assert rec["detail"]["cost_usd"] > 0, rec
        # The card keeps the running sum rounded to 6 places (Session.add_recap_cost).
        assert s.wire()["recap_cost_usd"] == round(rec["detail"]["cost_usd"], 6), (
            s.wire()
        )
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 30)


FLASH = "opencode-go/deepseek-v4-flash"


async def test_a_real_opencode_session(tmp_path: Path):
    """A prompt, an aegis tool call, an interrupt, a resume that keeps the
    context, and a read session refused an edit. About a cent of Go."""
    import asyncio

    import uvicorn

    from aegis.app import App
    from aegis.roots import make_roots
    from aegis.web import build_web

    from .test_agents import _free_port

    opencode = shutil.which("opencode")
    assert opencode, "opencode is not on PATH"
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n"
        f"  deep: {{harness: opencode, model: {FLASH}, effort: high, permission: full}}\n"
        f"  reader: {{harness: opencode, model: {FLASH}, effort: high, permission: read}}\n"
    )
    port = _free_port()
    app = App(
        make_roots(tmp_path, None),
        opencode_bin=opencode,
        base_url=f"http://127.0.0.1:{port}",
    )
    server = uvicorn.Server(
        uvicorn.Config(
            build_web(app, "t", {f"127.0.0.1:{port}"}), port=port, log_level="warning"
        )
    )
    task = asyncio.create_task(server.serve())
    await until(lambda: server.started, timeout=10, what="uvicorn")

    def tools(s) -> list[dict]:
        return [e for e in s.entries() if e["kind"] == "tool"]

    def prose(s) -> list[str]:
        return [e["md"] for e in s.entries() if e["kind"] == "prose"]

    async def turn(s, text: str) -> None:
        await s.send(text)
        await until(lambda: s.status == "working", timeout=30, what="the turn to start")
        await until(lambda: s.status == "idle", timeout=120, what=f"the turn {text!r}")

    try:
        r = await app.registry.call("session.spawn", {"agent": "deep"})
        s = app.sessions.sessions[r["log_id"]]
        await turn(s, "Remember the word PELICAN. Reply with the single word OK.")
        assert s.cost_usd and s.context_window and s.resume_id.startswith("ses_")

        await turn(
            s, "Call the aegis meta tool, then tell me in one line what it returned."
        )
        assert any(t["title"] == "meta" and t["status"] == "ok" for t in tools(s))

        await s.send("Run exactly this bash command in the foreground: sleep 40")
        await until(
            lambda: any(t["status"] == "running" for t in tools(s)),
            timeout=120,
            what="the bash call",
        )
        await s.interrupt()
        await until(
            lambda: s.status == "idle", timeout=20, what="idle after the interrupt"
        )
        assert tools(s)[-1]["status"] == "err"
        assert tools(s)[-1]["detail"]["result"] == "interrupted"

        sid = s.resume_id
        await s.stop()
        await turn(s, "What word did I ask you to remember? Reply with that one word.")
        assert "PELICAN" in prose(s)[-1].upper() and s.resume_id == sid

        r = await app.registry.call("session.spawn", {"agent": "reader"})
        reader = app.sessions.sessions[r["log_id"]]
        await turn(
            reader, "Use the write tool to create a file named x.txt containing hi."
        )
        assert not (tmp_path / "x.txt").exists()
        # OpenCode drops a denied tool from the toolset, so a read session is
        # never offered write, edit or bash; the full one makes the same file.
        assert not [t for t in tools(reader) if t["title"] in ("Write", "Edit", "Bash")]
        await turn(s, "Use the write tool to create a file named y.txt containing hi.")
        assert (tmp_path / "y.txt").exists()
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 30)
        await app.shutdown()


async def test_a_real_agent_hands_off_across_a_link(tmp_path: Path, fake_claude):
    """A real Haiku on alpha finds handle@server in its primer and hands off to
    a session on beta, a linked server; beta's session gets the header."""
    from .test_links import Node, inbox

    claude = shutil.which("claude")
    assert claude, "claude is not on PATH"
    beta = await Node(tmp_path / "beta", "beta", fake_claude).start()
    alpha = Node(tmp_path / "alpha", "alpha", claude)
    (alpha.root / ".aegis.yaml").write_text(
        f"agents:\n  haiku: {{harness: claude-code, model: {HAIKU}, effort: low, permission: full}}\n"
    )
    await alpha.start()
    try:
        alpha.app.links.add("beta", beta.base, beta.token)
        await until(
            lambda: alpha.app.links.get("beta").state == "linked",
            timeout=10,
            what="the link",
        )
        far = await beta.spawn()
        r = await alpha.app.registry.call("session.spawn", {"agent": "haiku"})
        s = alpha.app.sessions.sessions[r["log_id"]]
        await s.send(
            f"Hand the message 'ping from alpha' to the session {far.handle}@beta with "
            "the aegis peer_handoff tool. Then reply with the single word DONE."
        )
        await until(lambda: inbox(far), timeout=120, what="the handoff on beta")
        (msg,) = inbox(far)
        assert (
            f"@alpha ({alpha.app.user})" in msg["md"] and "ping from alpha" in msg["md"]
        )
    finally:
        await alpha.stop()
        await beta.stop()
