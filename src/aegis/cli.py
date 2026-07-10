from __future__ import annotations

import asyncio
import signal
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path

import typer
from rich.console import Console

from aegis.config import (
    ConfigError, find_project_root, load_config, load_queues,
)
from aegis.core.manager import SessionManager
from aegis.drivers import DRIVERS, get_driver
from aegis.mcp import AegisMCP
from aegis.state.workspace import CorruptWorkspace, state_dir
from aegis.tui import AegisApp
from aegis.tui.app import pick_workspace_to_resume

app = typer.Typer(add_completion=False, no_args_is_help=False)
_console = Console()

from aegis.cli_schedule import app as _schedule_app  # noqa: E402
app.add_typer(_schedule_app, name="schedule")

from aegis.cli_budget import app as _budget_app  # noqa: E402
app.add_typer(_budget_app, name="budget")

from aegis.cli_models import app as _models_app  # noqa: E402
app.add_typer(_models_app, name="models")

from aegis.cli_config import app as _config_app  # noqa: E402
app.add_typer(_config_app, name="config")

from aegis.cli_plugin import app as _plugin_app  # noqa: E402
app.add_typer(_plugin_app, name="plugin")


def _version_callback(value: bool) -> None:
    if value:
        try:
            v = _pkg_version("aegis-harness")
        except PackageNotFoundError:        # not installed (rare in dev)
            v = "0.0.0+unknown"
        typer.echo(f"aegis {v}")
        raise typer.Exit()


# `aegis init` was retired in the .aegis.yaml migration. To bootstrap:
#   - Run `aegis` in an empty directory — the TUI opens the ConfigPanel
#     so you can add agents interactively.
#   - Or use the scriptable CLI verbs:
#     `aegis config agent add main --provider claude-code --model opus
#                                  --effort high`.


@app.callback(invoke_without_command=True)
def run(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True,
        help="Show version and exit."),
    agent: str = typer.Option(None, "--agent", "-a",
                              help="Named agent profile to use."),
    cwd: str = typer.Option(".", "--cwd",
                            help="Working dir for the harness subprocess."),
    clean: bool = typer.Option(
        False, "--clean",
        help="Ignore prior workspace state; start fresh"),
) -> None:
    """Run the interactive aegis session (default when no subcommand)."""
    if ctx.invoked_subcommand is not None:
        return
    # Bootstrap mode: no .aegis.yaml anywhere → drop straight into the
    # TUI ConfigPanel instead of refusing. Once the user saves at least
    # one agent + a default_agent, normal session spawn becomes
    # available (currently via app relaunch — slice 16 will make this
    # in-place via the watchdog reload path).
    root = find_project_root() or Path.cwd()
    voice_cfg = None
    if not (root / ".aegis.yaml").is_file():
        agents: dict = {}
        default_agent = ""
        queues: dict = {}
    else:
        try:
            agents, default_agent = load_config()
        except ConfigError as e:
            _console.print(f"[red]{e}[/red]")
            raise typer.Exit(1)
        name = agent or default_agent
        if name not in agents:
            _console.print(
                f"[red]Unknown agent {name!r}. "
                f"Known: {sorted(agents)}[/red]")
            raise typer.Exit(1)
        default_agent = name
        try:
            queues = load_queues(root)
        except ConfigError as e:
            _console.print(f"[red]{e}[/red]")
            raise typer.Exit(1)
        from aegis.config.yaml_loader import load_config as _load_yaml
        try:
            voice_cfg = _load_yaml(root).voice
        except ConfigError:
            voice_cfg = None

    effective_cwd = str(root) if cwd == "." else cwd

    try:
        pick_workspace_to_resume(state_dir(Path.cwd()), clean=clean)
    except CorruptWorkspace as e:
        typer.echo(f"aegis: {e}", err=True)
        typer.echo("hint: re-run with `aegis --clean` to ignore prior state.",
                   err=True)
        raise typer.Exit(code=2)

    # Best-effort background refresh of ~/.cache/aegis/models.yaml so
    # prices + context windows stay current without a release. Never
    # blocks startup; failures are silent.
    try:
        from aegis.models.refresh import maybe_refresh
        maybe_refresh()
    except Exception:  # noqa: BLE001
        pass

    def make_session(profile, mcp_url, handle):
        return get_driver(profile.harness).session(
            profile, effective_cwd, mcp_url, handle)

    # Driver registry for workspace resume — one instance per provider so
    # bootstrap_resume can call drv.resume(...) without re-instantiating
    # per tab.
    drivers = {slug: cls() for slug, cls in DRIVERS.items()}
    AegisApp(agents, default_agent, make_session, AegisMCP(),
             queues=queues, clean=clean, drivers=drivers,
             cwd=effective_cwd, voice=voice_cfg).run()


@dataclass
class _PlaneBridge:
    queue_manager: object
    inbox_router: object
    workflow_registry: object = None
    state_root: object = None
    scheduler: object = None
    _inline_schedule_names: set = field(default_factory=set)

    def inline_schedule_names(self) -> set:
        return set(self._inline_schedule_names)


async def _maybe_start_remote_plane(cfg, queue_manager) -> "_PlaneBridge | None":
    """Start the remote plane if `.aegis.yaml` configured it.

    No-op when ``cfg.remote_plane`` is None. Otherwise builds the
    Starlette app + an asyncio task running uvicorn. Also installs the
    callback observer when ``cfg.remotes`` is non-empty.

    Returns the bridge so the caller can attach a scheduler later.
    """
    if getattr(cfg, "remote_plane", None) is None:
        return None
    from types import SimpleNamespace

    from aegis.remote import plane as plane_mod
    from aegis.remote.callback_observer import install_callback_observer
    from aegis.workflow.decorator import get_workflow
    root = Path.cwd()
    bridge = _PlaneBridge(
        queue_manager=queue_manager,
        inbox_router=getattr(queue_manager, "_inbox", None),
        workflow_registry=SimpleNamespace(get=get_workflow),
        state_root=root,
        _inline_schedule_names=set(getattr(cfg, "inline_schedule_names", set())),
    )
    app = plane_mod.build_plane(bridge, cfg.remote_plane)
    plane_mod.run_plane_async(app, cfg.remote_plane.bind)
    remotes = getattr(cfg, "remotes", None) or {}
    if remotes and cfg.remote_plane.peer_name:
        install_callback_observer(
            queue_manager, remotes=remotes,
            self_peer_name=cfg.remote_plane.peer_name)
    elif remotes:
        import logging
        logging.getLogger(__name__).info(
            "remote_plane.peer_name not set; outbound callback observer "
            "not installed. aegis_enqueue(target=..., callback=True) "
            "will return an error at call time. Set "
            "remote_plane.peer_name in .aegis.yaml to enable callbacks.")
    return bridge


def _aegis_version() -> str:
    """aegis package version, used as the web PWA cache-bust key."""
    try:
        from importlib.metadata import version
        return version("aegis-harness")
    except Exception:
        return "0"


async def _serve(*, agents, default_agent, make_session, mcp,
                 stop: asyncio.Event, queues: dict | None = None,
                 schedules: dict | None = None,
                 remotes: dict | None = None,
                 remote_plane=None, web=None,
                 inline_schedule_names: set[str] | None = None) -> None:
    from aegis.queue import InboxRouter, QueueManager

    inbox = InboxRouter()
    mgr = SessionManager(agents, default_agent, make_session, mcp,
                         inbox=inbox)
    qm = QueueManager(queues or {}, mgr, inbox)
    mgr.attach_queue_manager(qm)
    # Canvas plane — shared markdown blackboards reachable via MCP.
    from pathlib import Path

    from aegis.canvas.manager import CanvasManager
    from aegis.canvas.notify import make_canvas_notifier
    from aegis.state.workspace import state_dir as _state_dir
    # Persist every serve-spawned session to JSONL (same state_dir the
    # WebFrontend reads from), so seq is a real disk line index in web mode.
    mgr.attach_persistence(_state_dir(Path.cwd()))
    # Persist the claims registry to the same state_dir the TUI uses, so
    # aegis_claim survives a serve restart and both frontends share one store.
    mgr.attach_locks_state(_state_dir(Path.cwd()))
    cm = CanvasManager(state_dir=_state_dir(Path.cwd()),
                       notifier=make_canvas_notifier(inbox))
    mgr.attach_canvas_manager(cm)
    from aegis.terminal.manager import TerminalManager
    from aegis.terminal.notify import make_terminal_notifier
    tm = TerminalManager(state_dir=_state_dir(Path.cwd()) / "terminals")
    tm.set_notifier(make_terminal_notifier(inbox))
    mgr.attach_terminal_manager(tm)
    mgr.attach_remotes(remotes or {})
    mgr.attach_remote_plane(remote_plane)
    mcp.bind(mgr)
    await mcp.start()
    await qm.start()

    from aegis.config.yaml_loader import AegisConfig as _AegisConfig
    plane_bridge = await _maybe_start_remote_plane(
        _AegisConfig(
            remote_plane=remote_plane,
            remotes=remotes or {},
            inline_schedule_names=set(inline_schedule_names or set()),
        ), qm)

    # Scheduler — only runs when schedules are configured.
    scheduler = None
    reload_watcher = None
    if schedules:
        from types import SimpleNamespace as _SN

        from aegis.scheduler import Scheduler
        from aegis.scheduler.reload import ReloadWatcher
        from aegis.workflow.decorator import get_workflow as _get_wf
        from aegis.workflow.runner import run_workflow as _rw

        async def _scheduler_run_workflow(name: str, args: dict):
            result = await _rw(name, args, bridge=mgr,
                               queue_manager=qm, inbox_router=inbox)
            if result.get("status") == "ok":
                return result.get("result")
            raise RuntimeError(result.get("error", "workflow failed"))

        scheduler = Scheduler(
            schedules=schedules, state_dir=_state_dir(Path.cwd()),
            run_workflow=_scheduler_run_workflow)
        if plane_bridge is not None:
            plane_bridge.scheduler = scheduler
        mgr.attach_scheduler_context(
            scheduler=scheduler, state_root=Path.cwd(),
            workflow_registry=_SN(get=_get_wf),
            inline_schedule_names=set(inline_schedule_names or set()))
        await scheduler.start()

        # Hot reload: re-read .aegis.yaml on filesystem change and
        # atomic-swap into the running scheduler. Parse errors keep
        # the old config intact.
        root = Path.cwd()

        def _on_reload() -> None:
            from aegis.config.yaml_loader import (
                import_plugins, load_config as _load_yaml,
            )
            cfg = _load_yaml(root)
            import_plugins(cfg)
            scheduler.replace_schedules(cfg.schedules)

        events_log = _state_dir(root) / "aegis_events.jsonl"
        reload_watcher = ReloadWatcher(
            root, on_reload=_on_reload, events_log=events_log)
        await reload_watcher.start()

    tasks = []
    if web is not None:
        from aegis.web.frontend import WebFrontend
        web_fe = WebFrontend(mgr, web, state_dir=_state_dir(Path.cwd()),
                             server_version=_aegis_version())
        tasks.append(asyncio.create_task(web_fe.run()))
        _console.print(f"[green]web UI on {web_fe.url}[/green]")
    try:
        await stop.wait()
    finally:
        for t in tasks:
            t.cancel()
        if reload_watcher is not None:
            await reload_watcher.stop()
        if scheduler is not None:
            await scheduler.stop()
        await qm.stop()
        await mgr.close_all()
        await mcp.stop()


@app.command()
def serve(cwd: str = typer.Option(".", "--cwd")) -> None:
    """Run the headless daemon (MCP plane + optional web frontend)."""
    _run_serve(cwd)


@app.command()
def web(cwd: str = typer.Option(".", "--cwd"),
        no_browser: bool = typer.Option(False, "--no-browser")) -> None:
    """Launch the web client: ensure a token, open the browser, then serve."""
    root = find_project_root() or Path.cwd()
    if not (root / ".aegis.yaml").is_file():
        _console.print("[red]No .aegis.yaml found.[/red]")
        raise typer.Exit(1)
    token = _ensure_web_token(root)
    from aegis.config import edit as _edit
    from aegis.config.yaml_loader import load_config as _load_yaml
    from aegis.state.workspace import state_dir as _sd
    from aegis.web.frontend import _resolve_port
    web_cfg = _load_yaml(root).web
    port = _resolve_port(web_cfg, _sd(root))
    _edit.set_web(root, port=port)
    url = f"http://{web_cfg.bind}:{port}/?t={token}"
    _console.print(f"[green]aegis web → {url}[/green]")
    if not no_browser:
        import threading
        import webbrowser
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    _run_serve(cwd)


def _ensure_web_token(root: Path) -> str:
    """Return the configured web token, generating + persisting a fresh one
    into .aegis.yaml when none is set. Idempotent."""
    import secrets

    from aegis.config import edit as _edit
    from aegis.config.yaml_loader import load_config as _load_yaml
    cfg = _load_yaml(root)
    if cfg.web and cfg.web.token:
        return cfg.web.token
    token = secrets.token_urlsafe(32)
    _edit.set_web(root, token=token)
    return token


def _run_serve(cwd: str) -> None:
    try:
        agents, default_agent = load_config()
    except ConfigError as e:
        _console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    root = find_project_root() or Path.cwd()
    effective = str(root) if cwd == "." else cwd

    def make_session(profile, mcp_url, handle):
        return get_driver(profile.harness).session(
            profile, effective, mcp_url, handle)

    try:
        queues = load_queues(root)
    except ConfigError as e:
        _console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    try:
        from aegis.config.yaml_loader import (
            import_plugins, load_config as _load_yaml,
        )
        yaml_cfg = _load_yaml(root)
        import_plugins(yaml_cfg)
        schedules = yaml_cfg.schedules
        remotes = yaml_cfg.remotes
        remote_plane = yaml_cfg.remote_plane
        inline_schedule_names = yaml_cfg.inline_schedule_names
        web = yaml_cfg.web if (yaml_cfg.web and yaml_cfg.web.token) else None
    except ConfigError as e:
        _console.print(f"[red]Failed to load .aegis.yaml: {e}[/red]")
        raise typer.Exit(1)

    async def main_async():
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        await _serve(agents=agents, default_agent=default_agent,
                     make_session=make_session, mcp=AegisMCP(),
                     stop=stop, queues=queues,
                     schedules=schedules,
                     remotes=remotes, remote_plane=remote_plane, web=web,
                     inline_schedule_names=inline_schedule_names)

    asyncio.run(main_async())


# Workflow subcommand group --------------------------------------------
workflow_app = typer.Typer(help="Author + run aegis workflows.")
app.add_typer(workflow_app, name="workflow")


@workflow_app.command("list")
def workflow_list_cmd() -> None:
    """List @workflow-decorated functions discovered via .aegis/plugins."""
    root = find_project_root() or Path.cwd()
    try:
        from aegis.config.yaml_loader import (
            import_plugins,
            load_config as _load_yaml,
        )
        import_plugins(_load_yaml(root))
    except ConfigError as e:
        _console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    from aegis.workflow import list_workflows
    names = list_workflows()
    if not names:
        _console.print("[yellow]no workflows registered.[/yellow]")
        return
    for n in names:
        typer.echo(n)


@workflow_app.command(
    "run",
    context_settings={"allow_extra_args": True,
                      "ignore_unknown_options": True})
def workflow_run_cmd(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="workflow name"),
) -> None:
    """Run a workflow by name. Pass kwargs as ``--key=value``.

    All kwargs arrive as strings; the workflow body coerces if needed.
    """
    try:
        agents, default_agent = load_config()
    except ConfigError as e:
        _console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    root = find_project_root() or Path.cwd()
    try:
        queues = load_queues(root)
        from aegis.config.yaml_loader import (
            import_plugins,
            load_config as _load_yaml,
        )
        import_plugins(_load_yaml(root))
    except ConfigError as e:
        _console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    # Parse trailing --key=value kwargs from ctx.args.
    kwargs: dict[str, str] = {}
    for tok in ctx.args:
        if not tok.startswith("--") or "=" not in tok:
            _console.print(f"[red]bad kwarg: {tok!r} (use --key=value)[/red]")
            raise typer.Exit(1)
        k, v = tok[2:].split("=", 1)
        kwargs[k.replace("-", "_")] = v

    from aegis.workflow import get_workflow, list_workflows, run_workflow

    if get_workflow(name) is None:
        _console.print(
            f"[red]unknown workflow: {name!r}. "
            f"Available: {list_workflows()}[/red]")
        raise typer.Exit(1)

    def make_session(profile, mcp_url, handle):
        return get_driver(profile.harness).session(
            profile, str(root), mcp_url, handle)

    async def main_async():
        from aegis.queue import InboxRouter, QueueManager
        inbox = InboxRouter(state_dir=root / ".aegis" / "state")
        mgr = SessionManager(agents, default_agent, make_session,
                             AegisMCP(), inbox=inbox)
        qm = QueueManager(queues, mgr, inbox,
                          state_dir=root / ".aegis" / "state")
        mgr.attach_queue_manager(qm)
        from aegis.canvas.manager import CanvasManager
        from aegis.canvas.notify import make_canvas_notifier
        cm = CanvasManager(state_dir=root / ".aegis" / "state",
                           notifier=make_canvas_notifier(inbox))
        mgr.attach_canvas_manager(cm)
        mgr._mcp.bind(mgr)
        await mgr._mcp.start()
        await qm.start()
        try:
            out = await run_workflow(
                name, kwargs, bridge=mgr, queue_manager=qm,
                inbox_router=inbox,
                state_dir=root / ".aegis" / "state")
        finally:
            await qm.stop()
            await mgr.close_all()
            await mgr._mcp.stop()
        return out

    out = asyncio.run(main_async())
    typer.echo(out["status"])
    if out["status"] == "ok":
        typer.echo(out.get("result", ""))
    else:
        typer.echo(out.get("error", ""))
        raise typer.Exit(1)


@workflow_app.command("status")
def workflow_status_cmd(
    workflow_id: str = typer.Argument(..., help="workflow id"),
) -> None:
    """Show the last recorded status from the ledger for ``workflow_id``.

    Reads ``.aegis/state/<id>/ledger.jsonl`` — useful for inspecting a
    completed or failed run. For live status of a workflow attached to a
    running daemon, call the ``aegis_workflow_status`` MCP tool instead.
    """
    import json
    root = find_project_root() or Path.cwd()
    ledger = root / ".aegis" / "state" / workflow_id / "ledger.jsonl"
    if not ledger.exists():
        _console.print(f"[red]no ledger for {workflow_id!r} at {ledger}[/red]")
        raise typer.Exit(1)
    records = [json.loads(line) for line in ledger.read_text().splitlines()
               if line.strip()]
    if not records:
        _console.print("[yellow]empty ledger[/yellow]")
        return
    last = records[-1]
    typer.echo(f"workflow_id: {workflow_id}")
    typer.echo(f"last kind:   {last.get('kind', '?')}")
    typer.echo(f"at:          {last.get('at', '?')}")
    if "name" in last:
        typer.echo(f"checkpoint:  {last['name']}")
    if last.get("kind") == "errored":
        typer.echo(f"error:       {last.get('error', '?')}")
    if last.get("kind") == "finished":
        typer.echo(f"result:      {last.get('result', '')}")


@workflow_app.command("cancel")
def workflow_cancel_cmd(
    workflow_id: str = typer.Argument(..., help="workflow id"),
) -> None:
    """Cancel a workflow attached to a running daemon.

    Requires a live runner; for hermetic CLI runs the workflow already
    finished when the command returns. Talk to the daemon via the
    ``aegis_workflow_cancel`` MCP tool instead.
    """
    _console.print(
        "[yellow]Cancel requires a running daemon. Use the "
        "aegis_workflow_cancel MCP tool against the live MCP server.[/yellow]")
    raise typer.Exit(1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
