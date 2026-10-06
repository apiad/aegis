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
        f"agents:\n  haiku: {{model: {HAIKU}, effort: low, permission: full}}\n"
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
        r = await app.registry.call("session.spawn", {"profile": "haiku"})
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
