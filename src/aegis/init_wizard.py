"""Interactive ``aegis init`` wizard.

Detects installed agent CLIs (`claude`, `gemini`, `opencode`), runs a
Rich-powered loop that builds an agents/queues config, and renders it
as a ``.aegis.py`` file using the provider-object form.

Public surface used by ``cli.py``:

- ``detect_providers()`` → list[ProviderSpec] annotated with `available`.
- ``render_aegis_py(config)`` → str (the file contents).
- ``run_wizard(console, providers=None)`` → WizardConfig | None.
- ``WizardConfig`` dataclass — agents + default_agent + queues, the
  intermediate representation between the wizard and the renderer.

The wizard refuses-if-upstream and `--force`-overrides logic lives in
``cli.py``, not here — this module is purely "given a console, build a
config dict; given a config dict, write a file."
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table


# ---------- Provider catalog -----------------------------------------

@dataclass
class ProviderSpec:
    """One agent CLI aegis knows how to drive."""

    name: str                       # "claude-code", "gemini", "opencode"
    cli: str                        # binary name on PATH
    cls_name: str                   # ClaudeCode / GeminiCLI / OpenCode
    model_shortlist: tuple[str, ...]
    permission_choices: tuple[str, ...]
    default_permission: str
    has_effort: bool                # True for Claude only
    available: bool = False         # filled by detect_providers


# Built-in catalog. Model shortlists deliberately small + opinionated;
# "other" is always offered as a free-text escape. Open-source-friendly
# bias for OpenCode (kimi, glm, minimax, qwen) — pick Claude-via-OpenCode
# is wasteful if you already have Claude direct.
_PROVIDER_CATALOG: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        name="claude-code", cli="claude", cls_name="ClaudeCode",
        model_shortlist=("opus", "sonnet", "haiku"),
        permission_choices=("read", "write", "full", "auto"),
        default_permission="auto",
        has_effort=True,
    ),
    ProviderSpec(
        name="gemini", cli="gemini", cls_name="GeminiCLI",
        model_shortlist=("gemini-3-flash-preview", "gemini-3-pro-preview"),
        permission_choices=("read", "write", "full", "auto"),
        default_permission="full",
        has_effort=False,
    ),
    ProviderSpec(
        name="opencode", cli="opencode", cls_name="OpenCode",
        model_shortlist=(
            "opencode/kimi-k2.6",
            "opencode/glm-5.1",
            "opencode/minimax-m2.7",
            "opencode/qwen3.6-plus",
        ),
        permission_choices=("read", "write", "full", "auto"),
        default_permission="full",
        has_effort=False,
    ),
)


def detect_providers(
    which: Callable[[str], str | None] = shutil.which,
) -> list[ProviderSpec]:
    """Return the catalog with `available` annotated. ``which`` is
    injected so tests can simulate any combination."""
    out = []
    for p in _PROVIDER_CATALOG:
        out.append(ProviderSpec(
            **{**p.__dict__, "available": which(p.cli) is not None}))
    return out


# ---------- Wizard result IR ------------------------------------------

@dataclass(frozen=True)
class AgentEntry:
    slug: str
    provider_name: str           # "claude-code" / "gemini" / "opencode"
    cls_name: str                # "ClaudeCode" / "GeminiCLI" / "OpenCode"
    model: str
    permission: str
    effort: str | None           # set for Claude only


@dataclass(frozen=True)
class QueueEntry:
    name: str
    agent_slug: str
    max_parallel: int


@dataclass
class WizardConfig:
    agents: list[AgentEntry] = field(default_factory=list)
    default_agent: str = ""
    queues: list[QueueEntry] = field(default_factory=list)


# ---------- Rendering -------------------------------------------------

def _render_agent(a: AgentEntry) -> str:
    """Render one agent dict entry as a Python expression line."""
    kwargs = [f'model="{a.model}"']
    if a.effort is not None:
        kwargs.append(f'effort="{a.effort}"')
    kwargs.append(f'permission="{a.permission}"')
    inner = ", ".join(kwargs)
    return f'    "{a.slug}": Agent(provider={a.cls_name}({inner})),'


def _render_queue(q: QueueEntry) -> str:
    return (f'    "{q.name}": '
            f'{{"agent": "{q.agent_slug}", '
            f'"max_parallel": {q.max_parallel}}},')


def render_aegis_py(config: WizardConfig) -> str:
    """Produce the contents of a ``.aegis.py`` file from a WizardConfig.

    The output uses the provider-object form (``Agent(provider=...)``)
    and only imports the provider classes actually needed.
    """
    used_cls = sorted({a.cls_name for a in config.agents})
    imports = ", ".join(["Agent", *used_cls]) if used_cls else "Agent"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    lines = [
        "# .aegis.py — Aegis configuration",
        f"# Generated by `aegis init` on {today}.",
        f"from aegis import {imports}",
        "",
        "agents = {",
    ]
    for a in config.agents:
        lines.append(_render_agent(a))
    lines.append("}")
    lines.append("")
    lines.append(f'default_agent = "{config.default_agent}"')
    lines.append("")

    if config.queues:
        lines.append("queues = {")
        for q in config.queues:
            lines.append(_render_queue(q))
        lines.append("}")
        lines.append("")

    return "\n".join(lines)


# ---------- Wizard ----------------------------------------------------

def _next_slug(existing: list[str], base: str) -> str:
    """Auto-suggest a slug: `base` if free, else `base-2`, `base-3`, …"""
    if base not in existing:
        return base
    i = 2
    while f"{base}-{i}" in existing:
        i += 1
    return f"{base}-{i}"


def _prompt_model(console: Console, provider: ProviderSpec) -> str:
    """Show the shortlist + 'other' and return the chosen/typed model."""
    options = list(provider.model_shortlist) + ["other (type your own)"]
    table = Table(show_header=False, box=None, pad_edge=False,
                  padding=(0, 1))
    for i, opt in enumerate(options, start=1):
        table.add_row(f"[cyan]{i}[/]", opt)
    console.print(table)
    choice = IntPrompt.ask(
        f"Model for {provider.name}",
        choices=[str(i) for i in range(1, len(options) + 1)],
        default=1, show_choices=False, console=console,
    )
    if choice <= len(provider.model_shortlist):
        return provider.model_shortlist[choice - 1]
    return Prompt.ask(
        f"Type model string for {provider.name}", console=console).strip()


def _prompt_agent(
    console: Console,
    available: list[ProviderSpec],
    existing_slugs: list[str],
) -> AgentEntry | None:
    """One add-agent step. Returns None if user picks 'done'."""
    options = [p.name for p in available] + ["done"]
    table = Table(show_header=False, box=None, pad_edge=False,
                  padding=(0, 1))
    for i, opt in enumerate(options, start=1):
        table.add_row(f"[cyan]{i}[/]", opt)
    console.print(table)
    n_agents = sum(1 for s in existing_slugs)
    default_choice = 1 if n_agents == 0 else len(options)  # nudge 'done' if you have some
    choice = IntPrompt.ask(
        "Add an agent" if n_agents == 0 else "Add another agent",
        choices=[str(i) for i in range(1, len(options) + 1)],
        default=default_choice, show_choices=False, console=console,
    )
    if choice == len(options):
        return None
    provider = available[choice - 1]

    model = _prompt_model(console, provider)
    permission = Prompt.ask(
        f"Permission for {provider.name}",
        choices=list(provider.permission_choices),
        default=provider.default_permission, console=console,
    )
    effort = None
    if provider.has_effort:
        effort = Prompt.ask(
            "Effort", choices=["low", "medium", "high", "max"],
            default="high", console=console,
        )

    suggested_slug = _next_slug(existing_slugs, provider.cli)
    while True:
        slug = Prompt.ask(
            "Agent slug", default=suggested_slug, console=console).strip()
        if not slug:
            console.print("[red]slug cannot be empty[/red]")
            continue
        if slug in existing_slugs:
            console.print(
                f"[red]slug {slug!r} already used; pick another[/red]")
            continue
        break

    return AgentEntry(
        slug=slug, provider_name=provider.name,
        cls_name=provider.cls_name,
        model=model, permission=permission, effort=effort,
    )


def _prompt_queue(
    console: Console,
    agent_slugs: list[str],
    existing_queue_names: list[str],
) -> QueueEntry | None:
    """One add-queue step. Returns None if user picks 'done'."""
    n = len(existing_queue_names)
    if not Confirm.ask(
        "Add a queue" if n == 0 else "Add another queue",
        default=(n == 0), console=console,
    ):
        return None

    while True:
        name = Prompt.ask("Queue name", console=console).strip()
        if not name:
            console.print("[red]name cannot be empty[/red]")
            continue
        if name in existing_queue_names:
            console.print(
                f"[red]queue {name!r} already exists; pick another[/red]")
            continue
        break

    table = Table(show_header=False, box=None, pad_edge=False,
                  padding=(0, 1))
    for i, slug in enumerate(agent_slugs, start=1):
        table.add_row(f"[cyan]{i}[/]", slug)
    console.print(table)
    choice = IntPrompt.ask(
        "Bind to which agent",
        choices=[str(i) for i in range(1, len(agent_slugs) + 1)],
        default=1, show_choices=False, console=console,
    )
    agent_slug = agent_slugs[choice - 1]
    max_parallel = IntPrompt.ask(
        "max_parallel", default=1, console=console)
    return QueueEntry(name=name, agent_slug=agent_slug,
                      max_parallel=max_parallel)


def run_wizard(
    console: Console,
    providers: list[ProviderSpec] | None = None,
) -> WizardConfig | None:
    """Drive the full add-agent + default + add-queue loops. Returns
    the assembled config, or None if the user has nothing usable
    (no installed CLIs / no agents added)."""
    providers = providers or detect_providers()
    available = [p for p in providers if p.available]
    detected = [p.cli for p in available]
    missing = [p.cli for p in providers if not p.available]

    console.print(Panel.fit(
        "[bold]aegis init[/bold] — wizard\n"
        f"Detected on PATH: "
        f"[green]{', '.join(detected) if detected else '(none)'}[/green]"
        + (f"\nNot found:        [dim]{', '.join(missing)}[/dim]"
           if missing else ""),
        border_style="cyan",
    ))

    if not available:
        console.print(
            "[red]No agent CLIs detected. Install at least one of "
            "claude, gemini, opencode and re-run.[/red]")
        return None

    config = WizardConfig()
    while True:
        existing_slugs = [a.slug for a in config.agents]
        entry = _prompt_agent(console, available, existing_slugs)
        if entry is None:
            break
        config.agents.append(entry)

    if not config.agents:
        console.print("[yellow]no agents configured; nothing to write.[/yellow]")
        return None

    # Default agent
    if len(config.agents) == 1:
        config.default_agent = config.agents[0].slug
    else:
        slugs = [a.slug for a in config.agents]
        table = Table(show_header=False, box=None, pad_edge=False,
                      padding=(0, 1))
        for i, slug in enumerate(slugs, start=1):
            table.add_row(f"[cyan]{i}[/]", slug)
        console.print(table)
        choice = IntPrompt.ask(
            "Which agent is the default (used when no --agent flag)",
            choices=[str(i) for i in range(1, len(slugs) + 1)],
            default=1, show_choices=False, console=console,
        )
        config.default_agent = slugs[choice - 1]

    # Queues
    agent_slugs = [a.slug for a in config.agents]
    while True:
        existing_queue_names = [q.name for q in config.queues]
        entry = _prompt_queue(console, agent_slugs, existing_queue_names)
        if entry is None:
            break
        config.queues.append(entry)

    return config
