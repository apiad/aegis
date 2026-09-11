"""Fails against main: the TUI path constructs AegisApp directly
(cli.py:224) and never wires a scheduler, so `aegis` silently does not fire
schedules while `aegis serve` does.
"""
from __future__ import annotations

import asyncio

import pytest
from typer.testing import CliRunner


class _StubMCP:
    """_serve calls mcp.bind/start/stop and reads mcp.port (cli.py:415-418,
    :497). mcp=None crashes before reaching anything under test — which
    would fail identically against a correct and a broken implementation.

    ``bound`` keeps every bridge it was handed, in order, so a test can say
    *which* object _serve bound rather than only that something was bound.
    """

    url = "http://127.0.0.1:0/mcp/"

    def __init__(self):
        self.port = 0
        self.bound: list = []
        self.starts = 0
        self.stops = 0

    def bind(self, mgr): self.bound.append(mgr)
    async def start(self):
        self.port = 12345
        self.starts += 1

    async def stop(self): self.stops += 1


class _RecordingUI:
    def __init__(self): self.manager = None
    async def run(self, manager): self.manager = manager


def _recording_app(captured: dict):
    """Stands in for AegisApp so the attachment's real ``run`` can be driven
    without a TTY. Captures the constructor kwargs — which is where the
    single-plane property is decided."""

    class _RecordingApp:
        def __init__(self, *args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs

        async def run_async(self):
            captured["ran"] = True

        def run(self):  # pragma: no cover - must never be taken
            raise AssertionError(
                "blocking .run() inside _serve's loop deadlocks; "
                "the attachment must use run_async()")

    return _RecordingApp


async def test_local_ui_boot_starts_a_scheduler(tmp_path):
    from aegis.cli import _serve
    from aegis.config.roots import AegisRoots

    ui = _RecordingUI()
    stop = asyncio.Event()
    stop.set()

    await _serve(
        roots=AegisRoots.for_project(tmp_path),
        agents={}, default_agent="", make_session=lambda *a, **k: None,
        mcp=_StubMCP(), stop=stop,
        schedules={"nightly": {"cron": "0 3 * * *", "workflow": "noop"}},
        ui=ui)

    assert ui.manager is not None, "the UI attachment never received a manager"
    assert ui.manager.scheduler is not None, (
        "a UI-attached boot must start the scheduler; this is the defect "
        "documented at tui/app.py:466")


async def test_headless_boot_still_works(tmp_path):
    from aegis.cli import _serve
    from aegis.config.roots import AegisRoots
    stop = asyncio.Event()
    stop.set()
    await _serve(roots=AegisRoots.for_project(tmp_path), agents={},
                 default_agent="", make_session=lambda *a, **k: None,
                 mcp=_StubMCP(), stop=stop, ui=None)


async def test_attachment_runs_on_the_one_mcp_serve_bound(tmp_path,
                                                          monkeypatch):
    """One boot, one MCP plane.

    ``is``-identity, not truthiness: an attachment that constructed its own
    ``AegisMCP()`` would give the app a perfectly working MCP server — on a
    second port, addressing a bridge that is not the one holding the brain.
    Every spawned agent would then call into a plane with no sessions on it.
    """
    from aegis.cli import LocalTuiAttachment, _serve
    from aegis.config.roots import AegisRoots

    captured: dict = {}
    monkeypatch.setattr("aegis.cli.AegisApp", _recording_app(captured))

    mcp = _StubMCP()
    factory = lambda *a, **k: None   # noqa: E731
    roots = AegisRoots.for_project(tmp_path)
    ui = LocalTuiAttachment(clean=True, agent=None, queues={}, voice=None,
                            hosts={}, host_registry=None, drivers={},
                            cwd=str(tmp_path), agents={}, roots=roots)
    stop = asyncio.Event()
    stop.set()

    await _serve(roots=roots, agents={}, default_agent="",
                 make_session=factory, mcp=mcp, stop=stop, ui=ui)

    kw = captured["kwargs"]
    assert captured.get("ran"), "the attachment never ran the app"
    assert mcp.bound, "_serve never bound the MCP plane"
    manager = mcp.bound[0]
    assert kw["bridge"] is manager, (
        "the app must be handed the very manager _serve wired, via bridge=")
    assert kw["mcp"] is mcp, (
        "the app must run on the MCP object _serve bound to the manager, "
        "not a second AegisMCP on a second port")
    assert kw["make_session"] is factory, (
        "a fresh session factory would spawn harnesses the manager does "
        "not know about; mcp=None/make_session=None crash outright")
    assert kw.get("manager") is None, (
        "manager= is the --remote path and disables the local plane")


async def test_attachment_passes_the_real_agent_objects(tmp_path,
                                                        monkeypatch):
    """`{slug: None}` is the --remote client's shape (cli.py:302), where
    there are no local Agent objects. Locally they back drv.resume,
    _resolve_place and the per-session model overlay."""
    from aegis.cli import LocalTuiAttachment
    from aegis.config import Agent
    from aegis.config.roots import AegisRoots
    from aegis.core.manager import SessionManager

    captured: dict = {}
    monkeypatch.setattr("aegis.cli.AegisApp", _recording_app(captured))

    agent = Agent(harness="claude-code", model="opus", effort="high",
                  permission="auto")
    hosts = {"box": object()}
    registry = object()
    roots = AegisRoots.for_project(tmp_path)
    mgr = SessionManager(agents={"main": agent}, default_agent="main",
                         make_session=lambda *a, **k: None, mcp=_StubMCP(),
                         roots=roots)
    ui = LocalTuiAttachment(clean=False, agent="main", queues={"q": object()},
                            voice=None, hosts=hosts, host_registry=registry,
                            drivers={}, cwd=str(tmp_path),
                            agents={"main": agent}, roots=roots)
    await ui.run(mgr)

    kw = captured["kwargs"]
    assert kw["agents"]["main"] is agent
    assert kw["hosts"] is hosts
    assert kw["host_registry"] is registry
    assert kw["default_agent"] == "main"


def test_run_routes_the_tui_through_serve(monkeypatch, tmp_path):
    """`aegis` with a config must boot the shared brain and attach the TUI,
    which is what makes it fire schedules."""
    from aegis.cli import app as cli_app

    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n  main:\n    provider: claude-code\n    model: opus\n"
        "default_agent: main\n"
        "schedules:\n  nightly:\n    cron: '0 3 * * *'\n"
        "    workflow: noop\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    seen: dict = {}

    async def _fake_serve(**kw):
        seen.update(kw)

    monkeypatch.setattr("aegis.cli._serve", _fake_serve)
    # Without this the pre-7b path reaches the real Textual app and the
    # test hangs instead of failing — a red phase nobody can read.
    monkeypatch.setattr(
        "aegis.tui.app.AegisApp.run",
        lambda self: (_ for _ in ()).throw(
            AssertionError("the TUI was launched outside _serve")))
    r = CliRunner().invoke(cli_app, [])
    assert r.exit_code == 0, r.output
    assert seen, "`aegis` did not route through _serve"
    assert seen["ui"] is not None, "`aegis` booted without a UI attachment"
    assert seen["schedules"], (
        "`aegis` dropped the configured schedules on the floor — the "
        "scheduler would never fire")
    assert seen["roots"].config_root == tmp_path.resolve()


def test_bootstrap_mode_without_config_does_not_exit_1(monkeypatch, tmp_path):
    """No .aegis.yaml anywhere → the ConfigPanel TUI, as before. Routing
    naively through the config loader would make a fresh directory exit 1."""
    from aegis.cli import app as cli_app

    monkeypatch.chdir(tmp_path)
    ran: dict = {}
    monkeypatch.setattr("aegis.tui.app.AegisApp.run",
                        lambda self: ran.setdefault("agents", self._agents))

    async def _fake_serve(**kw):  # pragma: no cover - must not be reached
        raise AssertionError("bootstrap must not boot a brain")

    monkeypatch.setattr("aegis.cli._serve", _fake_serve)
    r = CliRunner().invoke(cli_app, [])
    assert r.exit_code == 0, r.output
    assert ran.get("agents") == {}, "bootstrap TUI never opened"


def test_corrupt_workspace_still_exits_2(monkeypatch, tmp_path):
    """pick_workspace_to_resume moves into the attachment; its
    CorruptWorkspace -> Exit(2) contract must move with it."""
    import typer

    from aegis.cli import LocalTuiAttachment
    from aegis.config.roots import AegisRoots
    from aegis.state.workspace import CorruptWorkspace

    def _boom(*_a, **_k):
        raise CorruptWorkspace("workspace.json is not JSON")

    monkeypatch.setattr("aegis.cli.pick_workspace_to_resume", _boom)
    ui = LocalTuiAttachment(clean=False, agent=None, queues={}, voice=None,
                            hosts={}, host_registry=None, drivers={},
                            cwd=str(tmp_path), agents={},
                            roots=AegisRoots.for_project(tmp_path))
    with pytest.raises(typer.Exit) as exc:
        asyncio.run(ui.run(object()))
    assert exc.value.exit_code == 2
