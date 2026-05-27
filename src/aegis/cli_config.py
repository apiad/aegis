"""`aegis config ...` — scriptable CLI for `.aegis.yaml`.

Every writing subcommand goes through `aegis.config.edit` helpers
(comment-preserving ruamel.yaml round-trip + atomic write + full
validation through yaml_loader.load_config before persisting).
"""
from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from aegis.config import ConfigError, find_project_root

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="Author and inspect .aegis.yaml.")
_console = Console()


def _resolve_root() -> Path:
    return find_project_root() or Path.cwd()


# --- top-level: show -------------------------------------------------

@app.command("show")
def show_cmd(
    as_json: bool = typer.Option(
        False, "--json", help="Emit JSON instead of YAML."),
) -> None:
    """Print the current .aegis.yaml (or a hint if none exists)."""
    root = _resolve_root()
    base = root / ".aegis.yaml"
    if not base.is_file():
        _console.print(
            f"[red]No .aegis.yaml at {base}.[/red]\n"
            f"[dim]Run [bold]aegis config agent add <slug> --provider "
            f"<...> --model <...>[/bold] to create one.[/dim]")
        raise typer.Exit(1)
    if as_json:
        from aegis.config import load_queues
        from aegis.config.yaml_loader import load_config as _load_yaml
        cfg = _load_yaml(root)
        queues = load_queues(root)
        out = {
            "default_agent": cfg.default_agent,
            "agents": {
                name: {
                    "provider": a.harness,
                    "model": a.model,
                    "permission": a.permission.value,
                    "effort": a.effort.value if a.harness == "claude-code"
                              else None,
                } for name, a in cfg.agents.items()
            },
            "queues": {
                name: {
                    "agent": q.agent_profile,
                    "max_parallel": q.max_parallel,
                    "budgets": [
                        {"constraint": b.constraint,
                         "limit": str(b.limit),
                         "window": b.window_str} for b in q.budgets
                    ] or None,
                } for name, q in queues.items()
            },
            "telegram": {
                "token": (cfg.telegram.token[:4] + "…"
                          if cfg.telegram and cfg.telegram.token else None),
                "chat_id": cfg.telegram.chat_id if cfg.telegram else None,
                "auto_prompt": cfg.telegram.auto_prompt
                               if cfg.telegram else None,
            },
            "plugin_dirs": [str(p) for p in cfg.plugin_dirs],
        }
        typer.echo(json.dumps(out, indent=2))
        return
    typer.echo(base.read_text())


# --- agent group -----------------------------------------------------

agent_app = typer.Typer(add_completion=False, no_args_is_help=True,
                        help="Manage agent profiles.")
app.add_typer(agent_app, name="agent")


@agent_app.command("list")
def agent_list_cmd() -> None:
    """List declared agent profiles."""
    root = _resolve_root()
    base = root / ".aegis.yaml"
    if not base.is_file():
        _console.print(
            "[yellow]no agents declared.[/yellow]\n"
            "[dim]Run [bold]aegis config agent add <slug> ...[/bold] to "
            "add one.[/dim]")
        return
    try:
        from aegis.config.yaml_loader import load_config as _load_yaml
        cfg = _load_yaml(root)
    except ConfigError as e:
        _console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    if not cfg.agents:
        _console.print("[yellow]no agents declared.[/yellow]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("slug")
    table.add_column("provider")
    table.add_column("model")
    table.add_column("effort")
    table.add_column("permission")
    table.add_column("default")
    for name, a in cfg.agents.items():
        is_default = "✓" if name == cfg.default_agent else ""
        effort = a.effort.value if a.harness == "claude-code" else "—"
        table.add_row(name, a.harness, a.model, effort,
                      a.permission.value, is_default)
    _console.print(table)


@agent_app.command("add")
def agent_add_cmd(
    slug: str = typer.Argument(..., help="Agent profile slug."),
    provider: str = typer.Option(
        ..., "--provider", "-p",
        help="claude-code | gemini | opencode"),
    model: str = typer.Option(..., "--model", "-m"),
    effort: str | None = typer.Option(
        None, "--effort",
        help="low|medium|high|max (claude-code only)"),
    permission: str | None = typer.Option(
        None, "--permission", help="read|write|full|auto"),
) -> None:
    """Add an agent profile. Creates .aegis.yaml if missing."""
    from aegis.config.edit import add_agent
    root = _resolve_root()
    try:
        add_agent(root, slug,
                  provider=provider, model=model,
                  effort=effort, permission=permission)
    except ConfigError as e:
        _console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    _console.print(f"[green]added agent {slug!r}[/green]")


@agent_app.command("remove")
def agent_remove_cmd(
    slug: str = typer.Argument(..., help="Agent profile slug."),
) -> None:
    """Remove an agent profile. Fails loud on cross-section references."""
    from aegis.config.edit import remove_agent
    root = _resolve_root()
    try:
        remove_agent(root, slug)
    except ConfigError as e:
        _console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    _console.print(f"[green]removed agent {slug!r}[/green]")


# --- queue group -----------------------------------------------------

queue_app = typer.Typer(add_completion=False, no_args_is_help=True,
                        help="Manage queues.")
app.add_typer(queue_app, name="queue")


def _parse_budget(spec: str) -> dict:
    """Parse `usd:1.00:1h` or `output_tokens:500000:1h` into a dict."""
    parts = spec.split(":")
    if len(parts) != 3:
        raise typer.BadParameter(
            f"--budget {spec!r}: expected '<constraint>:<limit>:<window>'")
    constraint, limit_s, window = parts
    if constraint not in {"usd", "output_tokens"}:
        raise typer.BadParameter(
            f"--budget {spec!r}: constraint must be usd or output_tokens")
    try:
        limit = float(limit_s) if constraint == "usd" else int(limit_s)
    except ValueError:
        raise typer.BadParameter(
            f"--budget {spec!r}: bad limit {limit_s!r}")
    return {constraint: limit, "window": window}


@queue_app.command("list")
def queue_list_cmd() -> None:
    from aegis.config import load_queues
    root = _resolve_root()
    base = root / ".aegis.yaml"
    if not base.is_file():
        _console.print("[yellow]no queues declared.[/yellow]")
        return
    try:
        queues = load_queues(root)
    except ConfigError as e:
        _console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    if not queues:
        _console.print("[yellow]no queues declared.[/yellow]")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("name")
    table.add_column("agent")
    table.add_column("max_parallel", justify="right")
    table.add_column("budgets")
    for name, q in queues.items():
        if q.budgets:
            budgets = ", ".join(
                f"{b.constraint}:{b.limit}/{b.window_str}" for b in q.budgets)
        else:
            budgets = "—"
        table.add_row(name, q.agent_profile, str(q.max_parallel), budgets)
    _console.print(table)


@queue_app.command("add")
def queue_add_cmd(
    name: str = typer.Argument(..., help="Queue name."),
    agent: str = typer.Option(..., "--agent", "-a",
                              help="Agent profile to bind."),
    max_parallel: int = typer.Option(
        ..., "--max-parallel", "-n",
        help="Max concurrent workers."),
    budget: list[str] = typer.Option(
        None, "--budget",
        help="Repeatable: '<constraint>:<limit>:<window>'. "
             "E.g. 'usd:1.00:1h' or 'output_tokens:500000:1h'."),
) -> None:
    """Add a queue. Fails loud on unknown agent ref + bad max_parallel."""
    from aegis.config.edit import add_queue
    root = _resolve_root()
    budgets = [_parse_budget(b) for b in (budget or [])] or None
    try:
        add_queue(root, name, agent=agent, max_parallel=max_parallel,
                  budgets=budgets)
    except ConfigError as e:
        _console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    _console.print(f"[green]added queue {name!r}[/green]")


@queue_app.command("remove")
def queue_remove_cmd(
    name: str = typer.Argument(..., help="Queue name."),
) -> None:
    """Remove a queue."""
    from aegis.config.edit import remove_queue
    root = _resolve_root()
    try:
        remove_queue(root, name)
    except ConfigError as e:
        _console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    _console.print(f"[green]removed queue {name!r}[/green]")


# --- telegram group --------------------------------------------------

telegram_app = typer.Typer(add_completion=False, no_args_is_help=True,
                           help="Configure the Telegram bot integration.")
app.add_typer(telegram_app, name="telegram")


@telegram_app.command("show")
def telegram_show_cmd() -> None:
    """Show telegram config (token is redacted)."""
    from aegis.config import load_telegram_config
    root = _resolve_root()
    try:
        tcfg = load_telegram_config(root)
    except ConfigError as e:
        _console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    redacted = (tcfg.token[:4] + "…(" + str(len(tcfg.token)) + "ch)"
                if tcfg.token else "—")
    typer.echo(f"token:       {redacted}")
    typer.echo(f"chat_id:     {tcfg.chat_id if tcfg.chat_id else '—'}")
    typer.echo(f"auto_prompt: {tcfg.auto_prompt!r}")


@telegram_app.command("set")
def telegram_set_cmd(
    token: str | None = typer.Option(None, "--token"),
    clear_token: bool = typer.Option(False, "--clear-token"),
    chat_id: int | None = typer.Option(None, "--chat-id"),
    clear_chat_id: bool = typer.Option(False, "--clear-chat-id"),
    auto_prompt: str | None = typer.Option(None, "--auto-prompt"),
    clear_auto_prompt: bool = typer.Option(False, "--clear-auto-prompt"),
) -> None:
    """Set or clear fields in the `telegram:` block. Unspecified flags
    leave the corresponding field alone."""
    from aegis.config.edit import UNCHANGED, set_telegram
    if token is not None and clear_token:
        raise typer.BadParameter("--token and --clear-token are mutually exclusive")
    if chat_id is not None and clear_chat_id:
        raise typer.BadParameter("--chat-id and --clear-chat-id are mutually exclusive")
    if auto_prompt is not None and clear_auto_prompt:
        raise typer.BadParameter(
            "--auto-prompt and --clear-auto-prompt are mutually exclusive")

    t_arg = None if clear_token else (token if token is not None else UNCHANGED)
    c_arg = None if clear_chat_id else (chat_id if chat_id is not None else UNCHANGED)
    a_arg = (None if clear_auto_prompt
             else (auto_prompt if auto_prompt is not None else UNCHANGED))

    root = _resolve_root()
    try:
        set_telegram(root, token=t_arg, chat_id=c_arg, auto_prompt=a_arg)
    except ConfigError as e:
        _console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    _console.print("[green]telegram updated[/green]")


# --- plugin-dir group ------------------------------------------------

plugin_dir_app = typer.Typer(
    add_completion=False, no_args_is_help=True,
    help="Manage plugin_dirs (auto-imported for @workflow registration).")
app.add_typer(plugin_dir_app, name="plugin-dir")


@plugin_dir_app.command("list")
def plugin_dir_list_cmd() -> None:
    root = _resolve_root()
    base = root / ".aegis.yaml"
    if not base.is_file():
        _console.print("[yellow]no plugin_dirs declared.[/yellow]")
        return
    try:
        from aegis.config.yaml_loader import load_config as _load_yaml
        cfg = _load_yaml(root)
    except ConfigError as e:
        _console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    if not cfg.plugin_dirs:
        _console.print("[yellow]no plugin_dirs declared.[/yellow]")
        return
    for p in cfg.plugin_dirs:
        # Render relative-to-root for terseness.
        try:
            rel = p.relative_to(root)
            typer.echo(str(rel))
        except ValueError:
            typer.echo(str(p))


@plugin_dir_app.command("add")
def plugin_dir_add_cmd(
    path: str = typer.Argument(..., help="Path (relative to project root)."),
) -> None:
    from aegis.config.edit import add_plugin_dir
    root = _resolve_root()
    try:
        add_plugin_dir(root, path)
    except ConfigError as e:
        _console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    _console.print(f"[green]added plugin_dir {path!r}[/green]")


@plugin_dir_app.command("remove")
def plugin_dir_remove_cmd(
    path: str = typer.Argument(..., help="Path to remove."),
) -> None:
    from aegis.config.edit import remove_plugin_dir
    root = _resolve_root()
    try:
        remove_plugin_dir(root, path)
    except ConfigError as e:
        _console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    _console.print(f"[green]removed plugin_dir {path!r}[/green]")


# --- default-agent --------------------------------------------------

@app.command("default-agent")
def default_agent_cmd(
    slug: str = typer.Argument(..., help="Slug to make the default."),
) -> None:
    """Set the default_agent (used when no --agent flag is given)."""
    from aegis.config.edit import set_default_agent
    root = _resolve_root()
    try:
        set_default_agent(root, slug)
    except ConfigError as e:
        _console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    _console.print(f"[green]default_agent → {slug!r}[/green]")
