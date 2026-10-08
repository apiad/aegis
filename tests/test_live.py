"""One real Claude Code session through aegis: a prompt, an interrupt, a stop
and a resume that keeps the context.

Spends a few cents of Haiku. Run with ``make test-live``."""

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
        assert s.context_window and s.context_tokens and s.claude_session_id

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


async def test_real_claude_ends_a_bash_call_on_a_counted_verdict(tmp_path: Path):
    """The primer's Bash paragraph reaches the real binary: a search whose
    plain answer ends on a file name instead ends on the count it found."""
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
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 30)
