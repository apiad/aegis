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
            "Use Bash to run `echo starting the job`, which stands for starting a "
            f"job that creates the file {flag}. Then use the aegis monitor_start tool "
            "to wait until that file exists "
            "(done condition `test -f <that path>`, interval 2 seconds). Then end your turn. "
            "When you are woken, reply with the single word WOKEN."
        )
        await until(
            lambda: app.monitors.of(s.log_id),
            timeout=120,
            what="the monitor armed by Claude",
        )
        [m] = app.monitors.of(s.log_id)
        bash = [e["id"] for e in s.entries() if e["title"] == "Bash"]
        assert m.source and m.source["id"] in bash, m.source  # #870
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


async def test_real_claude_waits_on_another_session_and_is_woken(tmp_path: Path):
    """The primer's monitor_sessions paragraph, followed by a real model. The
    watched session runs on Sonnet: Haiku at low effort ended its turn without
    turn_end, which a waiter rightly does not read as finished."""
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
        f"  sonnet: {{harness: claude-code, model: {SONNET}, effort: low, permission: full}}\n"
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
        busy = app.sessions.sessions[r["log_id"]]
        r = await app.registry.call("session.spawn", {"agent": "haiku"})
        s = app.sessions.sessions[r["log_id"]]
        await busy.send(
            "Run the shell command `sleep 25`, then reply with the word SLEPT."
        )
        await until(lambda: busy.status == "working", timeout=60, what="busy working")
        await s.send(
            f"Wait, using aegis, until the session {busy.handle} has finished, "
            "then reply with the single word FINISHED."
        )
        await until(
            lambda: any(m.sessions for m in app.monitors.of(s.log_id)),
            timeout=120,
            what="a session monitor armed by Claude",
        )
        await until(
            lambda: any(
                "FINISHED" in (e.get("md") or "")
                for e in s.entries()
                if e["kind"] == "prose"
            ),
            timeout=180,
            what="the wake and Claude's answer",
        )
        calls = [e["title"] for e in s.entries() if e["kind"] == "tool"]
        assert "monitor_sessions" in calls
        wakes = [e["title"] for e in s.entries() if e["kind"] == "inbox"]
        assert any(" · ok · " in w for w in wakes), wakes
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 30)


async def test_real_claude_sends_a_file_and_the_link_serves_it(tmp_path: Path):
    """The real binary finds file_send from its description and hands over two
    files it wrote in one call; the entry's links serve the same bytes."""
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
            "Write two files in your working directory: haiku.md, a three-line "
            "poem about pelicans, and notes.txt, one line saying why. Then hand "
            "both to me in a single call of the aegis tool for sending files, "
            "captioned 'A pelican haiku'. Then end your turn."
        )
        await until(
            lambda: (
                s.status == "idle" and any(e["kind"] == "file" for e in s.entries())
            ),
            timeout=180,
            what="the file sent by Claude",
        )
        (e,) = [e for e in s.entries() if e["kind"] == "file"]
        sent = {f["name"]: f for f in e["detail"]["files"]}
        assert set(sent) == {"haiku.md", "notes.txt"}
        assert sent["haiku.md"]["preview"] == "markdown"
        async with httpx.AsyncClient() as c:
            for name, f in sent.items():
                got = await c.get(base + f["url"])
                assert got.content == (tmp_path / name).read_bytes()
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


async def test_real_claude_builds_a_controls_and_preview_canvas(tmp_path: Path):
    """The real binary docks a page of controls and the report they shape,
    one under the other, from dock= alone; a slider's release reaches it as
    the browser would send it, and it sends the report's next version."""
    import asyncio
    import re

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
    (tmp_path / "report.md").write_text(
        "# Quarterly report\n\nSales grew in every region this quarter.\n"
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

    def report_versions() -> list[int]:
        return [
            f["version"]
            for e in s.entries()
            if e["kind"] == "file"
            for f in e["detail"]["files"]
            if f["name"] == "report.md"
        ]

    def column_of(pane: str) -> list[str] | None:
        for w in s.layout["windows"]:
            for c in w["columns"]:
                ids = [p["id"] for p in c["panes"]]
                if pane in ids:
                    return ids
        return None

    try:
        r = await app.registry.call("session.spawn", {"agent": "haiku"})
        s = app.sessions.sessions[r["log_id"]]
        await s.send(
            "Send me report.md and a page with a font-size slider, dock the page "
            "to the right and the report below it, and when the slider is "
            "released, rewrite report.md with that size noted in its first line "
            "and send it again. Then end your turn and wait for the slider."
        )
        await until(
            lambda: (
                s.status == "idle"
                and any(e["kind"] == "artifact" for e in s.entries())
                and report_versions()
            ),
            timeout=240,
            what="the page and the report",
        )
        (art,) = [e for e in s.entries() if e["kind"] == "artifact"]
        assert art["status"] == "live"
        (key,) = {
            f["key"]
            for e in s.entries()
            if e["kind"] == "file"
            for f in e["detail"]["files"]
            if f["name"] == "report.md"
        }
        assert column_of(art["id"]) == [art["id"], key], s.layout
        # The slider's release, as the page's own code emits it.
        async with httpx.AsyncClient() as c:
            page = (await c.get(base + art["detail"]["url"])).text
        named = re.search(r"aegis\.emit\(\s*[\"'`]([a-z][a-z0-9_-]*)", page)
        assert named, "the page emits nothing"
        await app.registry.call(
            "artifact.emit",
            {
                "log_id": s.log_id,
                "artifact_id": art["id"],
                "name": named.group(1),
                "data": {"size": 22},
            },
        )
        await until(
            lambda: s.status == "idle" and 2 in report_versions(),
            timeout=240,
            what="the report's second version",
        )
        assert "22" in (tmp_path / "report.md").read_text().splitlines()[0]
        assert column_of(key) == [art["id"], key], "the pane kept its place"
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
            "CI is running on pull request 12 of the GitHub repo syalia-srl/aegis. Wait "
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


async def test_real_claude_searches_the_help_before_answering_how(tmp_path: Path):
    """The primer's help paragraph, followed by a real model (#339): asked how to
    do something in aegis, its first aegis call is help_search, not an answer
    from the primer or from memory. plan_update and turn_end are bookkeeping
    the primer asks for on any turn, so they do not count as a first call."""
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
            "How do I wait on a CI run in aegis? Answer in three lines. "
            "Do not start a wait yourself."
        )
        await until(
            lambda: s.status == "idle" and s.cost_usd,
            timeout=180,
            what="the answer",
        )
        aegis = {op.tool_name for op in app.registry.agent_ops()}
        calls = [
            e["title"]
            for e in s.entries()
            if e["kind"] == "tool" and e["title"] in aegis - {"plan_update", "turn_end"}
        ]
        assert calls and calls[0] == "help_search", calls
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 30)


async def test_a_real_agent_uses_a_knowledge_memory_from_the_hint(tmp_path: Path):
    """The knowledge hint, followed by a real model (#694): a memory the agent
    was never told about is named in the hint on the message, and the agent
    reads it with memory_read to answer. The description leaves the host out,
    so only a read gives the answer."""
    import asyncio

    import uvicorn

    from aegis.app import App
    from aegis.knowledge.items import Item
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
        await app.knowledge.store.save(
            app.user,
            Item(
                "memory",
                "quill-staging",
                "Which host project quill deploys to for staging",
                "The staging host for project quill is ben-staging-2.\n",
                {"type": "project"},
            ),
            "test",
        )
        r = await app.registry.call("session.spawn", {"agent": "sonnet"})
        s = app.sessions.sessions[r["log_id"]]
        await s.send("which host do I deploy quill to? answer with the host only")
        await until(
            lambda: s.status == "idle" and s.cost_usd,
            timeout=180,
            what="the answer",
        )
        said = " ".join(e.get("md") or "" for e in s.entries() if e["kind"] == "prose")
        assert "ben-staging-2" in said, said
        events = [
            r["event"] for r in app.knowledge.usage.rows(app.user, "quill-staging")
        ]
        assert "hinted" in events and "read" in events, events
        assert events.index("hinted") < events.index("read"), events
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 30)


async def test_a_real_agent_calls_a_skill_tool(tmp_path: Path):
    """A skill's MCP server, used by a real model (#695): the agent loads a
    skill that brings a server, learns its tool from skill_read, and calls it
    through skill_tool. The usage log shows the call."""
    import asyncio

    import uvicorn

    from aegis.app import App
    from aegis.knowledge.items import Item
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
    echo = Path(__file__).parent / "fixtures" / "skill_mcp_echo.py"
    try:
        await app.knowledge.store.save(
            app.user,
            Item(
                "skill",
                "echoer",
                "Use when asked to echo a text through the echoer skill's tool",
                "1. Call the skill's echo tool with the text.\n"
                "2. Answer with what it returned, word for word.\n",
                {"aegis": {"mcp": {"command": ["python3", "server.py"]}}},
            ),
            "test",
            extra={"server.py": echo.read_bytes()},
        )
        r = await app.registry.call("session.spawn", {"agent": "sonnet"})
        s = app.sessions.sessions[r["log_id"]]
        await s.send(
            "Use the echoer skill to echo the text pineapple-42, and answer with "
            "exactly what its tool returned"
        )
        await until(
            lambda: s.status == "idle" and s.cost_usd,
            timeout=180,
            what="the answer",
        )
        said = " ".join(e.get("md") or "" for e in s.entries() if e["kind"] == "prose")
        assert "echo: pineapple-42" in said, said
        rows = app.knowledge.usage.rows(app.user, "echoer")
        assert ("ran", "tool=echo") in [(r["event"], r["detail"]) for r in rows], rows
        assert s.skills == ["echoer"]
    finally:
        await app.skill_servers.shutdown()
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


async def test_real_claude_writes_its_answer_after_turn_end_once(tmp_path: Path):
    """turn_end says what the person has seen (#679). A status line before the
    work is not the answer, so the answer follows the call; an answer written
    before the call is not repeated after it. The current Sonnet at medium:
    claude-sonnet-5 at low effort skipped turn_end in 1 of 1 runs."""
    import asyncio
    import difflib

    import uvicorn

    from aegis.app import App
    from aegis.roots import make_roots
    from aegis.web import build_web

    from .test_agents import _free_port

    claude = shutil.which("claude")
    assert claude, "claude is not on PATH"
    (tmp_path / "geom.py").write_text(
        "def area(r):\n    return 3.14 * r * r\n\n"
        "def volume(r):\n    return 4/3 * 3.14 * r ** 2\n"
    )
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n  sonnet: {harness: claude-code, model: sonnet, effort: medium, permission: full}\n"
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

    def split(s, since: int) -> tuple[str, str]:
        # The prose of this turn before the last turn_end, and after it.
        es = s.entries()[since:]
        ends = [
            i
            for i, e in enumerate(es)
            if e["kind"] == "tool" and e["title"] == "turn_end"
        ]
        assert ends, [
            (e["kind"], e["title"], (e.get("md") or e.get("summary") or "")[:80])
            for e in es
        ]
        prose = lambda part: " ".join(e["md"] for e in part if e["kind"] == "prose")  # noqa: E731
        return prose(es[: ends[-1]]), prose(es[ends[-1] + 1 :])

    try:
        r = await app.registry.call("session.spawn", {"agent": "sonnet"})
        s = app.sessions.sessions[r["log_id"]]
        app.sessions.rename(s.log_id, "seen-live", "Live turn_end test")
        mark = len(s.entries())
        await s.send(
            "First tell me in one line what you are about to do. Then read geom.py "
            "and tell me whether it has bugs. Do not change anything."
        )
        await until(lambda: s.status == "idle" and s.cost_usd, 180, "the review turn")
        before, after = split(s, mark)
        assert "volume" in after and len(after) > len(before), (before, after)

        mark = len(s.entries())
        await s.send(
            "Explain in a paragraph why r ** 3 is right. Write your full answer "
            "first and call turn_end as the very last thing you do."
        )
        await until(
            lambda: s.status == "idle" and len(s.entries()) > mark + 2,
            180,
            "the answer-first turn",
        )
        before, after = split(s, mark)
        assert len(before) > 200 and len(after) < len(before) / 2, (before, after)
        assert difflib.SequenceMatcher(None, before, after).ratio() < 0.5
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 30)


async def test_real_claude_drafts_feedback_and_files_nothing_before_a_yes(
    tmp_path: Path, monkeypatch
):
    """/feedback, followed by a real model (#497): it reads the help page
    feedback, shows a draft, ends its turn asking, and runs no gh issue create.
    gh on PATH is a stub that logs its arguments and says it is logged in, so
    nothing can be published."""
    import asyncio
    import os

    import uvicorn

    from aegis.app import App
    from aegis.roots import make_roots
    from aegis.web import build_web

    from .test_agents import _free_port

    claude = shutil.which("claude")
    assert claude, "claude is not on PATH"
    stub = tmp_path / "bin"
    stub.mkdir()
    log = tmp_path / "gh.log"
    (stub / "gh").write_text(f'#!/bin/sh\necho "$*" >> {log}\necho "[]"\n')
    (stub / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{stub}{os.pathsep}{os.environ['PATH']}")
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
        await app.registry.call(
            "session.send",
            {
                "log_id": s.log_id,
                "text": "/feedback the help page for queues does not say how to "
                "cancel a task",
            },
        )
        await until(
            lambda: s.status == "idle" and s.cost_usd,
            timeout=240,
            what="the draft",
        )
        tools = [e for e in s.entries() if e["kind"] == "tool"]
        assert any(
            e["title"] == "help_read" and "feedback" in json.dumps(e) for e in tools
        ), [e["title"] for e in tools]
        assert s.wire()["attention"] == "needs_you", s.wire()
        logged = log.read_text() if log.exists() else ""
        assert "issue create" not in logged, logged
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 30)


async def test_real_claude_in_read_calls_its_card_and_still_cannot_write(
    tmp_path: Path,
):
    """#305: Claude Code's plan mode, which read maps to, refused every aegis
    tool. A real read session now calls turn_end and plan_update after a live
    effort change (the hook rides in the flag settings that change writes to),
    aegis refuses it session_spawn, and plan mode still refuses a write."""
    import asyncio

    import uvicorn

    from aegis.app import App
    from aegis.roots import make_roots
    from aegis.web import build_web

    from .test_agents import _free_port

    claude = shutil.which("claude")
    assert claude, "claude is not on PATH"
    (tmp_path / ".aegis.yaml").write_text(
        f"agents:\n  ro: {{harness: claude-code, model: {SONNET}, effort: low, permission: read}}\n"
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
        r = await app.registry.call("session.spawn", {"agent": "ro"})
        s = app.sessions.sessions[r["log_id"]]
        await s.send("Reply with the single word OK.")
        await until(lambda: s.status == "idle" and s.cost_usd, timeout=120, what="OK")
        await s.configure(effort="medium")
        before = s.cost_usd
        await s.send(
            "This tests permissions, so attempt every call even if you expect a "
            "refusal. 1) Call plan_update with one item 'probe' in state doing. "
            f"2) Write the file {tmp_path / 'w.txt'} containing w. 3) Call "
            "session_spawn with agent ro and prompt hi. 4) Tell me in one line "
            "which calls were refused, then end the turn with turn_end."
        )
        await until(
            lambda: s.status == "idle" and s.cost_usd != before,
            timeout=180,
            what="the probe turn",
        )
        assert s.standing["ended"]["how"] == "reported", s.standing
        assert s.standing.get("plan"), s.standing
        assert not (tmp_path / "w.txt").exists()
        assert len(app.sessions.sessions) == 1
        tools = " ".join(json.dumps(e) for e in s.entries() if e["kind"] == "tool")
        assert "read_only" in tools, tools[-2000:]
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 30)


async def test_real_claude_answers_both_nudges(tmp_path: Path, monkeypatch):
    """A real Sonnet told to end a turn with no tool call is asked, once, what
    it is doing, and answers with turn_end; one that leaves its plan untouched
    through a stretch of work is reminded once. The clocks are
    shortened to seconds."""
    import asyncio

    import uvicorn

    from aegis import nudges
    from aegis.app import App
    from aegis.roots import make_roots
    from aegis.web import build_web

    from .test_agents import _free_port

    monkeypatch.setattr(nudges, "EVERY_S", 1.0)
    monkeypatch.setattr(nudges.Nudges, "timings", lambda self: (5.0, 3600.0))
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

    def nudged(s, kind):
        return [
            e
            for e in s.entries()
            if e["kind"] == "inbox" and e["title"].startswith(f"aegis:nudge · {kind}")
        ]

    try:
        r = await app.registry.call("session.spawn", {"agent": "sonnet"})
        s = app.sessions.sessions[r["log_id"]]
        await s.send(
            "This is a test of aegis itself. Reply with the single word OK and end "
            "your turn without calling any tool at all, not even turn_end."
        )
        await until(lambda: nudged(s, "idle"), timeout=150, what="the idle nudge")
        await until(lambda: s.status == "idle", timeout=120, what="the answer")
        assert s.standing.get("report"), "the nudge was not answered with turn_end"
        await asyncio.sleep(8)
        assert len(nudged(s, "idle")) == 1

        monkeypatch.setattr(nudges.Nudges, "timings", lambda self: (3600.0, 8.0))
        r = await app.registry.call("session.spawn", {"agent": "sonnet"})
        p = app.sessions.sessions[r["log_id"]]
        await p.send(
            "This is a test of aegis itself. Call plan_update with two items, "
            "'wait' doing and 'report' pending. Then run exactly this bash command "
            "in the foreground: sleep 12. Then reply with the single word SLEPT, "
            "without updating the plan."
        )
        await until(lambda: nudged(p, "plan"), timeout=180, what="the plan nudge")
        await until(lambda: p.status == "idle", timeout=120, what="the answer")
        # Sonnet answers either way: it updates the plan, or says with
        # turn_end why it did not (here, the prompt told it not to).
        assert p.standing.get("report") or p.standing["plan_mark"] > 0
        await asyncio.sleep(8)
        assert len(nudged(p, "plan")) == 1
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


async def test_a_real_agent_reaches_back_over_a_trusted_link(
    tmp_path: Path, fake_claude
):
    """A real Haiku on beta, which alpha links with trust, finds alpha's session
    in session_list, hands off to it as handle@alpha over the link alpha opened,
    and reads it back with peer_read (#684)."""
    from .test_links import Node, inbox

    claude = shutil.which("claude")
    assert claude, "claude is not on PATH"
    alpha = await Node(tmp_path / "alpha", "alpha", fake_claude).start()
    beta = Node(tmp_path / "beta", "beta", claude)
    (beta.root / ".aegis.yaml").write_text(
        f"agents:\n  haiku: {{harness: claude-code, model: {HAIKU}, effort: low, permission: full}}\n"
    )
    await beta.start()
    try:
        alpha.app.links.add("beta", beta.base, beta.token, trusted=True)
        await until(
            lambda: beta.app.links.get("alpha") is not None,
            timeout=10,
            what="the way back",
        )
        near = await alpha.spawn()
        r = await beta.app.registry.call("session.spawn", {"agent": "haiku"})
        s = beta.app.sessions.sessions[r["log_id"]]
        await s.send(
            f"Hand the message 'ping from beta' to the session {near.handle}@alpha "
            "with the aegis peer_handoff tool, then read that session with "
            "peer_read and reply with the single word DONE."
        )
        await until(lambda: inbox(near), timeout=120, what="the handoff on alpha")
        (msg,) = inbox(near)
        assert "@beta (" in msg["md"] and "ping from beta" in msg["md"]
        await until(
            lambda: any(
                e["kind"] == "tool" and "peer_read" in json.dumps(e)
                for e in s.entries()
            ),
            timeout=120,
            what="the read back",
        )
    finally:
        await beta.stop()
        await alpha.stop()


CODEX_FREE = "openrouter/nvidia/nemotron-3-super-120b-a12b:free"


async def test_a_real_codex_session(tmp_path: Path, monkeypatch):
    """A prompt, an aegis tool call, a steer, an interrupt, a resume that keeps
    the context, and a read session whose write fails. A free model: costs
    nothing, and a 429 from its shared pool skips the test."""
    import asyncio
    import os

    import uvicorn

    from aegis.app import App
    from aegis.roots import make_roots
    from aegis.web import build_web

    from .test_agents import _free_port

    codex = shutil.which("codex")
    if not codex or not os.environ.get("OPENROUTER_API_KEY"):
        pytest.skip("needs codex on PATH and OPENROUTER_API_KEY")
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "config.toml").write_text(
        "[model_providers.openrouter]\n"
        'name = "OpenRouter"\n'
        'base_url = "https://openrouter.ai/api/v1"\n'
        'env_key = "OPENROUTER_API_KEY"\n'
        'wire_api = "responses"\n'
    )
    monkeypatch.setenv("CODEX_HOME", str(home))
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n"
        f"  cx: {{harness: codex, model: {CODEX_FREE}, effort: high, permission: full}}\n"
        f"  reader: {{harness: codex, model: {CODEX_FREE}, effort: high, permission: read}}\n"
    )
    port = _free_port()
    app = App(
        make_roots(tmp_path, None), codex_bin=codex, base_url=f"http://127.0.0.1:{port}"
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
        await until(lambda: s.status == "idle", timeout=180, what=f"the turn {text!r}")
        if any("429" in e["summary"] for e in s.entries() if e["kind"] == "error"):
            pytest.skip("the free model is rate-limited upstream")

    try:
        r = await app.registry.call("session.spawn", {"agent": "cx"})
        s = app.sessions.sessions[r["log_id"]]
        await turn(s, "Remember the word PELICAN. Reply with the single word OK.")
        assert s.context_window and s.resume_id and s.cost_usd == 0

        await turn(
            s, "Call the aegis meta tool, then tell me in one line what it returned."
        )
        assert any(t["title"] == "meta" and t["status"] == "ok" for t in tools(s))

        await s.send("Run exactly this shell command in the foreground: sleep 40")
        await until(
            lambda: any(t["status"] == "running" for t in tools(s)),
            timeout=120,
            what="the call",
        )
        await s.interrupt()
        await until(
            lambda: s.status == "idle", timeout=20, what="idle after the interrupt"
        )
        assert tools(s)[-1]["status"] == "err"

        sid = s.resume_id
        await s.stop()
        await turn(s, "What word did I ask you to remember? Reply with that one word.")
        assert "PELICAN" in prose(s)[-1].upper() and s.resume_id == sid

        r = await app.registry.call("session.spawn", {"agent": "reader"})
        reader = app.sessions.sessions[r["log_id"]]
        await turn(reader, "Run this shell command: echo hi > x.txt")
        assert not (tmp_path / "x.txt").exists()
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 30)
        await app.shutdown()


async def test_a_real_cursor_session(tmp_path: Path):
    """A prompt, an aegis tool call over the MCP endpoint Cursor fetches
    itself, an interrupt (which has no cancel behind it: the child is killed),
    a resume that keeps the context, and a read session refused a write.
    Needs a logged-in `cursor-agent`; its own subscription pays."""
    import asyncio

    import uvicorn

    from aegis.app import App
    from aegis.roots import make_roots
    from aegis.web import build_web

    from .test_agents import _free_port

    cursor = shutil.which("cursor-agent")
    assert cursor, "cursor-agent is not on PATH"
    # The installed version is whatever this machine runs — a pre-release the
    # tested range cannot compare against — so the test accepts it the way a
    # person does when aegis asks once.
    import subprocess

    raw = subprocess.run(
        [cursor, "--version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=120,
    ).stdout.strip()
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n"
        "  cx: {harness: cursor, model: default, effort: high, permission: full}\n"
        "  reader: {harness: cursor, model: default, effort: high, permission: read}\n"
        f"harness_versions: {{accepted: {{cursor: [{raw!r}]}}}}\n"
    )
    port = _free_port()
    app = App(
        make_roots(tmp_path, None),
        cursor_bin=cursor,
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
        await until(lambda: s.status == "working", timeout=60, what="the turn to start")
        await until(lambda: s.status == "idle", timeout=300, what=f"the turn {text!r}")

    try:
        r = await app.registry.call("session.spawn", {"agent": "cx"})
        s = app.sessions.sessions[r["log_id"]]
        await turn(s, "Remember the word PELICAN. Reply with the single word OK.")
        assert s.resume_id and prose(s)

        await turn(
            s, "Call the aegis meta tool, then tell me in one line what it returned."
        )
        assert any(
            t["status"] == "ok" and "meta" in (t["title"] or "") for t in tools(s)
        )

        # No cancel over ACP: the interrupt ends the turn by killing the child.
        await s.send("Run exactly this shell command in the foreground: sleep 40")
        await until(
            lambda: any(t["status"] == "running" for t in tools(s)),
            timeout=180,
            what="the bash call",
        )
        await s.interrupt()
        await until(
            lambda: s.status == "idle", timeout=20, what="idle after the interrupt"
        )
        assert tools(s)[-1]["status"] == "err"

        sid = s.resume_id
        await s.stop()
        await turn(s, "What word did I ask you to remember? Reply with that one word.")
        assert "PELICAN" in prose(s)[-1].upper() and s.resume_id == sid

        r = await app.registry.call("session.spawn", {"agent": "reader"})
        reader = app.sessions.sessions[r["log_id"]]
        await turn(
            reader, "Use your edit or write tool to create a file named x.txt with hi."
        )
        assert not (tmp_path / "x.txt").exists()
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 30)
        await app.shutdown()


async def test_real_claude_passes_a_brief_past_handoff_at_and_hands_off(tmp_path: Path):
    """The context line in turn_end's answer, followed by a real model (#354):
    at a finished task past a low handoff_at, it calls turn_end again with a
    brief, and session.handoff continues the work in a fresh session that
    archives the old one after its first turn."""
    import asyncio

    import uvicorn

    from aegis.app import App
    from aegis.ops import Caller
    from aegis.roots import make_roots
    from aegis.web import build_web

    from .test_agents import _free_port

    claude = shutil.which("claude")
    assert claude, "claude is not on PATH"
    (tmp_path / ".aegis.yaml").write_text(
        f"agents:\n  sonnet: {{harness: claude-code, model: {SONNET}, effort: low, permission: full}}\n"
        "context: {handoff_at: 5000}\n"
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
            "Write the word ALPHA into a file named notes.txt in your working "
            "directory. That finishes this task; report it as done."
        )
        await until(
            lambda: (s.standing.get("report") or {}).get("handoff"),
            timeout=180,
            what="a turn_end carrying a handoff brief",
        )
        brief = s.standing["report"]["handoff"]
        assert "notes.txt" in brief or "ALPHA" in brief, brief
        await until(lambda: s.status == "idle", timeout=60, what="the turn's end")
        r = await app.registry.call(
            "session.handoff", {"log_id": s.log_id}, Caller("user")
        )
        b = app.sessions.sessions[r["log_id"]]
        await until(
            lambda: s.log_id in app.sessions.archived,
            timeout=180,
            what="the old session archived after the new one's first turn",
        )
        assert b.spec.continues == s.log_id
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 30)


async def test_real_claude_hands_off_with_the_tool_and_the_new_session_acts(
    tmp_path: Path,
):
    """A real session calls session_handoff (#874): its session locks at the
    call, a new one starts when its turn ends and does what msg says, and the
    old one is archived after that first turn."""
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
            "Call the aegis session_handoff tool with msg set to: Write the word "
            "BETA into a file named handed.txt in your working directory. Then "
            "end your turn."
        )
        await until(
            lambda: s.log_id in app.sessions.archived,
            timeout=240,
            what="the old session archived after the new one's first turn",
        )
        assert (tmp_path / "handed.txt").read_text().strip() == "BETA"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 30)


async def test_real_btw_answers_mid_turn_and_the_agent_never_receives_it(
    tmp_path: Path,
):
    """A side note asked while a real session runs a long Bash call: the
    answer comes from the transcript, the turn goes on, and nothing of the
    side note is ever sent to claude."""
    from .test_agents import World

    claude = shutil.which("claude")
    assert claude, "claude is not on PATH"
    (tmp_path / ".aegis.yaml").write_text(
        f"default_agent: haiku\nagents:\n"
        f"  haiku: {{harness: claude-code, model: {HAIKU}, effort: low, permission: full}}\n"
        "btw: {agent: haiku}\n"
    )
    w = await World(tmp_path, claude).start()
    try:
        r = await w.app.registry.call("session.spawn", {"agent": "haiku"})
        s = w.app.sessions.sessions[r["log_id"]]
        await s.send("Remember the secret word HERON. Reply with the single word OK.")
        await until(lambda: s.status == "idle" and s.cost_usd, timeout=90, what="OK")
        await s.send(
            "Run exactly this bash command in the foreground: python3 -c 'import time; time.sleep(30)'"
        )
        await until(
            lambda: any(e["kind"] == "tool" for e in s.entries()),
            timeout=90,
            what="the Bash call",
        )
        q = "what is the secret word I asked you to remember?"
        await w.app.registry.call(
            "session.send", {"log_id": s.log_id, "text": f"/btw {q}"}
        )
        await until(
            lambda: any(
                e["kind"] == "btw" and e["status"] != "running" for e in s.entries()
            ),
            timeout=90,
            what="the side note's answer",
        )
        (e,) = [e for e in s.entries() if e["kind"] == "btw"]
        assert e["status"] == "ok", e["detail"]
        assert "HERON" in e["detail"]["answer"].upper()
        assert s.status == "working", "the turn went on while the side note ran"
        await until(lambda: s.status == "idle", timeout=90, what="the turn's end")
        sent = [
            r.get("text", "")
            for r in read_store(w.app.sessions.store_path(s.log_id))[0]
            if r.get("kind") == "send"
        ]
        assert sent and not any("secret word I asked" in t for t in sent)
        assert s.wire()["btw_cost_usd"] > 0
    finally:
        await w.stop()


def _pids_running(argv: list[str]) -> list[int]:
    """The pids whose command line is exactly ``argv``, read from /proc; the
    test kills what it started by pid."""
    want = "\0".join(argv).encode() + b"\0"
    out = []
    for d in Path("/proc").iterdir():
        try:
            if d.name.isdigit() and (d / "cmdline").read_bytes() == want:
                out.append(int(d.name))
        except OSError:
            pass
    return out


async def test_real_claude_parks_when_idle_and_not_while_a_background_bash_runs(
    tmp_path: Path, monkeypatch
):
    """#415 with a real Claude Code: a session that leaves a background Bash
    running keeps its process; one that only says done parks within seconds,
    and the next message resumes it with its context. The park clock is
    shortened to 5 s and the loop to 1 s."""
    import asyncio
    import os
    import signal

    import uvicorn

    from aegis import park
    from aegis.app import App
    from aegis.roots import make_roots
    from aegis.web import build_web

    from .test_agents import _free_port

    monkeypatch.setattr(park, "EVERY_S", 1.0)
    claude = shutil.which("claude")
    assert claude, "claude is not on PATH"
    (tmp_path / ".aegis.yaml").write_text(
        f"agents:\n  sonnet: {{harness: claude-code, model: {SONNET}, effort: low, permission: full}}\n"
        "park: {claude-code: 5}\n"
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
    sleep = ["sleep", "60.4137"]  # an unusual value, to find it by its argv
    try:
        r = await app.registry.call("session.spawn", {"agent": "sonnet"})
        a = app.sessions.sessions[r["log_id"]]
        await a.send(
            "This is a test of aegis itself. Run exactly this bash command with "
            f"run_in_background set to true: {' '.join(sleep)}. Do not wait for it. "
            "Then call turn_end with attention done and nothing else."
        )
        await until(
            lambda: a.status == "idle" and a.standing.get("report"),
            timeout=120,
            what="the background turn",
        )
        assert _pids_running(sleep), "the background sleep is not running"
        await asyncio.sleep(15)
        why = park.blockers(app.parker.facts(a, deep=True))
        assert a.asleep_why != "idle" and a.running, why
        assert any("background task" in w or "process" in w for w in why), why

        r = await app.registry.call("session.spawn", {"agent": "sonnet"})
        b = app.sessions.sessions[r["log_id"]]
        await b.send(
            "This is a test of aegis itself. Remember the word PELICAN. Call "
            "turn_end with attention done and do nothing else."
        )
        await until(
            lambda: b.status == "idle" and b.standing.get("report"),
            timeout=120,
            what="the done turn",
        )
        await until(lambda: b.asleep_why == "idle", timeout=15, what="the park")
        assert not b.running and b.parked_rss > 0

        def prose():
            return [e["md"] for e in b.entries() if e["kind"] == "prose"]

        before = len(prose())
        await b.send("Say the word you were given. Reply with just the word.")
        await until(
            lambda: b.status == "idle" and len(prose()) > before,
            timeout=120,
            what="the resumed turn",
        )
        assert b.running
        said = prose()
        assert "PELICAN" in said[-1].upper(), said[-3:]
    finally:
        for pid in _pids_running(sleep):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        server.should_exit = True
        await asyncio.wait_for(task, 30)


async def test_real_claude_loops_over_aegis_call_when_a_result_needs_processing(
    tmp_path: Path,
):
    """`aegis call`, chosen by a real model (#436): a count over several tool
    results is a Bash loop. The primer's paragraph alone moved Sonnet in 0 of 9
    runs; the shell form at the end of each tool's description, where the model
    picks a tool, moved it in 4 of 4. At low effort it may still read a page
    through the tool as well."""
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
            "For each of the aegis help pages monitors, queues, handoffs, links, "
            "artifacts and schedules, report how many words the text help_read "
            "returns for it has, as `wc -w` counts them."
        )
        await until(lambda: s.status == "working", timeout=60, what="the turn")
        await until(lambda: s.status == "idle", timeout=300, what="the turn's end")
        tools = [e for e in s.entries() if e["kind"] == "tool"]
        loops = [
            e
            for e in tools
            if e["title"] == "Bash" and "aegis call" in json.dumps(e.get("detail"))
        ]
        assert loops, [e["title"] for e in tools]
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 30)


async def test_a_real_agent_given_a_disk_notice_frees_only_what_it_created(
    tmp_path: Path, monkeypatch
):
    """The disk notice's text, followed by a real model: a spawned session
    deletes the file it made, leaves a shared cache it did not make, and asks
    nobody. The host's real reading is used, so df agrees with the notice;
    on: 1 makes it fire on any disk."""
    import asyncio

    import uvicorn

    import aegis.app
    from aegis.app import App
    from aegis.notices import Notices
    from aegis.ops import Caller
    from aegis.roots import make_roots
    from aegis.transcript.entries import NOTICE_HEADER
    from aegis.web import build_web

    from .test_agents import _free_port

    monkeypatch.setattr(aegis.app, "Notices", Notices)  # not conftest's calm one
    claude = shutil.which("claude")
    assert claude, "claude is not on PATH"
    agents = (
        f"agents:\n  sonnet: {{harness: claude-code, model: {SONNET}, "
        "effort: low, permission: full}\n"
    )
    config = tmp_path / ".aegis.yaml"
    config.write_text(
        agents + "notices: {disk: false, memory: false, cpu: false, quota: false}\n"
    )
    work = tmp_path / "work"
    work.mkdir()
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
        parent = await app.registry.call("session.spawn", {"agent": "sonnet"})
        r = await app.registry.call(
            "session.spawn",
            {"agent": "sonnet", "cwd": str(work)},
            Caller("agent", log_id=parent["log_id"]),
        )
        s = app.sessions.sessions[r["log_id"]]
        assert s.spec.spawned_by == parent["log_id"]
        await s.send(
            "Make the build output for this task: run "
            "`mkdir -p build && head -c 52428800 /dev/zero > build/out.bin` here. "
            "Then reply OK."
        )
        await until(lambda: s.status == "working", timeout=60, what="the first turn")
        await until(lambda: s.status == "idle", timeout=180, what="its end")
        own, shared = work / "build" / "out.bin", work / "cache" / "shared.bin"
        assert own.stat().st_size == 52428800
        shared.parent.mkdir()
        shared.write_bytes(b"\0" * 52428800)
        first = len(s.entries())

        config.write_text(
            agents + "notices: {disk: {on: 1, critical: 100, clear: 0}, "
            "memory: false, cpu: false, quota: false}\n"
        )
        await s.send("carry on")
        await until(lambda: s.status == "working", timeout=60, what="the second turn")
        await until(lambda: s.status == "idle", timeout=300, what="its end")
        records, _ = read_store(app.sessions.store_path(s.log_id))
        said = [x["text"] for x in records if x.get("kind") == "send"][-1]
        assert said.startswith("carry on\n\n" + NOTICE_HEADER + "disk · warn"), said
        later = s.entries()[first:]
        bash = [
            json.dumps(e.get("detail"))[:300]
            for e in later
            if e["kind"] == "tool" and e["title"] == "Bash"
        ]
        prose = [e["md"] for e in later if e["kind"] == "prose"]
        what = {"bash": bash, "final": prose[-1] if prose else None}
        (tmp_path / "what-the-agent-did.json").write_text(json.dumps(what, indent=1))
        assert not own.exists(), what
        assert shared.exists(), what
        assert prose and "?" not in prose[-1], what
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 30)


async def test_real_claude_is_woken_by_a_stalled_monitor_and_acts(tmp_path: Path):
    """A real Sonnet arms a monitor whose progress never moves and ends its
    turn to wait. With stall_min shortened to seconds, the stall warning
    wakes it, once, and it acts on it (cancels the monitor or says so)
    without being told what a stall is (#522)."""
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
        "notices:\n  monitors: {stall_min: 0.1}\n"
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

    def stalls(s):
        return [
            e
            for e in s.entries()
            if e["kind"] == "inbox" and ":stalled · warn" in e["title"]
        ]

    try:
        r = await app.registry.call("session.spawn", {"agent": "sonnet"})
        s = app.sessions.sessions[r["log_id"]]
        await s.send(
            "This is a test of aegis itself. Call monitor_start with description "
            '"tests", done "false", progress "echo 40" and interval_s 1, then end '
            "your turn to wait for it, without calling anything else."
        )
        await until(
            lambda: app.monitors.items and s.status == "idle",
            timeout=150,
            what="the monitor armed and the turn ended",
        )
        await until(lambda: stalls(s), timeout=60, what="the stall wake")
        await until(lambda: s.status == "idle", timeout=150, what="the answer to it")
        said = " ".join(e["md"] for e in s.entries() if e["kind"] == "prose").lower()
        assert not app.monitors.items or "stall" in said, (
            "the agent neither cancelled the monitor nor mentioned the stall"
        )
        await asyncio.sleep(5)
        assert len(stalls(s)) == 1
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 30)


async def test_real_claude_takes_a_notice_at_its_next_tool_call(tmp_path: Path):
    """A warning set while a real Sonnet's first Bash runs reaches it at its
    second Bash, inside the turn, and starts no turn of its own (#880)."""
    import asyncio

    import uvicorn

    from aegis.app import App
    from aegis.notices import Item
    from aegis.roots import make_roots
    from aegis.transcript.store import read_store
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
            "This is a test of aegis itself. Run `sleep 3` with Bash, then run "
            "`echo two` with Bash, in two separate calls, then reply in one line."
        )
        await until(
            lambda: any(e["kind"] == "tool" for e in s.entries()),
            timeout=120,
            what="the first Bash",
        )
        app.notices.set(s.log_id, "monitors", [Item("live:on", "warn", "steer-mark-7")])
        await until(lambda: s.status == "idle", timeout=180, what="the turn's end")
        records, _ = read_store(app.sessions.store_path(s.log_id))
        store = json.dumps(records)
        assert "steer-mark-7" in store, "the notice never reached the agent"
        rows = [e["kind"] for e in s.entries()]
        assert any(
            e["kind"] == "inbox" and "steer-mark-7" in (e.get("md") or "")
            for e in s.entries()
        ), rows
        sends = [x for x in records if x.get("kind") == "send"]
        results = [
            x
            for x in records
            if '"type":"result"' in str(x.get("line")).replace(" ", "")
        ]
        assert len(sends) == 1 and "steer-mark-7" not in sends[0]["text"]
        assert len(results) == 1, "a steered notice started a turn of its own"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 30)


async def test_real_claude_follows_know_how_it_was_told_about_mid_turn(tmp_path: Path):
    """No instruction names know-how. A real Sonnet started outside a repository
    touches it, gets its menu, and follows a rule only the doc states (#881)."""
    import asyncio
    import subprocess

    import uvicorn

    from aegis.app import App
    from aegis.roots import make_roots
    from aegis.web import build_web

    from .test_agents import _free_port

    claude = shutil.which("claude")
    assert claude, "claude is not on PATH"
    quill = tmp_path / "repos" / "quill"
    (quill / "know-how").mkdir(parents=True)
    (quill / "widget.py").write_text("print('hello')\n")
    (quill / "know-how" / "landing-a-change.md").write_text(
        "---\nwhen: committing any change in this repository\n---\n\n"
        "The commit subject must start with `WID:`. CI rejects anything else.\n"
    )
    git = ["git", "-C", str(quill), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "init", "-q", str(quill)], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "init"], check=True)
    subprocess.run(["git", "-C", str(quill), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(quill), "config", "user.email", "t@t"], check=True)
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
            "In repos/quill, change the greeting in widget.py to 'hola' and commit it."
        )
        await until(lambda: s.status == "working", timeout=60, what="the turn")
        await until(lambda: s.status == "idle", timeout=240, what="its end")
        subject = subprocess.run(
            ["git", "-C", str(quill), "log", "-1", "--format=%s"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        rows = [e["title"] for e in s.entries() if e["kind"] == "inbox"]
        assert [t for t in rows if "· know-how · quill" in t], rows
        assert subject.startswith("WID:"), subject
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 30)


async def test_real_claude_runs_setup_asks_first_and_proposes_from_a_project(
    tmp_path: Path, monkeypatch
):
    """/setup, followed by a real model (#612): it reads the help page setup,
    asks before reading transcripts, and once told to skip them proposes, on
    one artifact, a workflow for the project's release skill and a schedule
    for the crontab line. crontab on PATH is a stub, so nothing on the host
    is read or changed."""
    import asyncio
    import os

    import uvicorn

    from aegis.app import App
    from aegis.roots import make_roots
    from aegis.web import build_web

    from .test_agents import _free_port

    claude = shutil.which("claude")
    assert claude, "claude is not on PATH"
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "crontab").write_text(
        "#!/bin/sh\necho '0 3 * * * claude -p \"audit the dependencies\"'\n"
    )
    (stub / "crontab").chmod(0o755)
    monkeypatch.setenv("PATH", f"{stub}{os.pathsep}{os.environ['PATH']}")
    skill = tmp_path / ".claude" / "skills" / "release"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: release\ndescription: Cut a release\n---\n\n"
        "1. Bump the version.\n2. Push and wait for CI to pass.\n3. Tag it.\n"
        "4. Wait for the publish job.\n5. Install the published package.\n"
    )
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
        await app.registry.call("session.send", {"log_id": s.log_id, "text": "/setup"})
        await until(
            lambda: s.status == "idle" and s.cost_usd, timeout=300, what="the ask"
        )
        tools = [e for e in s.entries() if e["kind"] == "tool"]
        assert any(
            e["title"] == "help_read" and "setup" in json.dumps(e) for e in tools
        ), [e["title"] for e in tools]
        assert s.wire()["attention"] == "needs_you", s.wire()
        assert not [e for e in s.entries() if e["kind"] == "artifact"]
        await s.send("Skip the transcripts. Look at the rest and propose.")
        await until(
            lambda: (
                s.status == "idle" and any(e["kind"] == "artifact" for e in s.entries())
            ),
            timeout=420,
            what="the proposal page",
        )
        (page,) = [e for e in s.entries() if e["kind"] == "artifact"][-1:]
        html = json.dumps(page) + "".join(
            p.read_text(errors="replace") for p in (tmp_path / ".aegis").rglob("*.html")
        )
        assert "release" in html and ("audit" in html or "03:00" in html), html[:2000]
        assert (tmp_path / ".aegis.yaml").read_text().count("agents:") == 1
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 30)


async def test_real_claude_reads_an_urgent_handoff_inside_its_turn(tmp_path: Path):
    """An urgent handoff sent while a real Claude Code runs a tool is read at
    the next tool boundary: the turn goes on calling tools after its row."""
    from .test_agents import World

    claude = shutil.which("claude")
    assert claude, "claude is not on PATH"
    (tmp_path / ".aegis.yaml").write_text(
        f"agents:\n  haiku: {{harness: claude-code, model: {HAIKU}, effort: low, permission: full}}\n"
    )
    world = await World(tmp_path, claude).start()
    try:
        r = await world.app.registry.call("session.spawn", {"agent": "haiku"})
        s = world.app.sessions.sessions[r["log_id"]]
        await s.send(
            "Run these three shell commands as three separate Bash calls, one "
            "after the other: `sleep 5`, `sleep 5`, `sleep 5`. Then reply in one line."
        )
        await until(
            lambda: any(e["kind"] == "tool" for e in s.entries()),
            timeout=90,
            what="the first tool call",
        )
        did = await s.deliver(
            "> from agent:peer · 2026-10-22T12:00:00Z",
            "Include the word PELICAN in your final reply.",
            urgent=True,
        )
        assert did == "steered"
        await until(lambda: s.status == "idle", timeout=180, what="the turn's end")
        kinds = [(e["kind"], e.get("detail") or {}) for e in s.entries()]
        at = next(i for i, (k, d) in enumerate(kinds) if k == "inbox")
        assert kinds[at][1].get("steered") is True
        assert any(k == "tool" for k, _ in kinds[at + 1 :]), kinds
        prose = [e.get("md") or "" for e in s.entries() if e["kind"] == "prose"]
        assert "PELICAN" in prose[-1]
    finally:
        await world.stop()
