from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal, Sequence

from pydantic import BaseModel, ValidationError, model_validator


DEFAULT_TELEGRAM_PROMPT = (
    "You are replying via telegram, keep response compact and focused "
    "if possible, only resort to long responses if it really matters")


@dataclass(frozen=True)
class TelegramConfig:
    token: str | None
    chat_id: int | None
    auto_prompt: str


def load_telegram_config(path: Path) -> TelegramConfig:
    ns: dict[str, object] = {}
    if path.is_file():
        exec(compile(path.read_text(), str(path), "exec"), ns)  # noqa: S102
    token = os.environ.get("AEGIS_TELEGRAM_TOKEN") or ns.get("telegram_token")
    chat_id = ns.get("telegram_chat_id")
    auto = ns.get("auto_add_to_telegram_prompt", DEFAULT_TELEGRAM_PROMPT)
    return TelegramConfig(
        token=token or None,
        chat_id=int(chat_id) if chat_id is not None else None,
        auto_prompt="" if auto == "" else str(auto))


class Permission(str, Enum):
    read = "read"
    write = "write"
    full = "full"
    auto = "auto"


class Effort(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    max = "max"


class _ProviderBase(BaseModel):
    """Base for provider config objects. Subclasses bind to a specific
    harness CLI (claude-code, gemini, opencode) and carry the per-provider
    fields that matter for that CLI."""
    model: str
    permission: Permission = Permission.auto


class ClaudeCode(_ProviderBase):
    """Anthropic's `claude` CLI. Has an `effort` field (low|medium|high|max)
    that no other provider currently exposes."""
    name: Literal["claude-code"] = "claude-code"
    effort: Effort = Effort.high


class GeminiCLI(_ProviderBase):
    """Google's `gemini` CLI. Model strings are bare gemini model names
    (e.g. ``gemini-3-flash-preview``, ``gemini-3.1-pro``). No `effort`
    field — Gemini doesn't expose one. Permission maps to its
    ``--approval-mode`` (read=plan, write=auto_edit, full=yolo,
    auto=default)."""
    name: Literal["gemini"] = "gemini"
    permission: Permission = Permission.full  # gemini headless ≈ yolo


class OpenCode(_ProviderBase):
    """OpenCode's `opencode` CLI. Model strings use the ``provider/model``
    format opencode expects (e.g. ``opencode/claude-sonnet-4-6``,
    ``opencode/gemini-3-flash``, ``opencode/gpt-5``). Run ``opencode
    models`` to list what's available on your install."""
    name: Literal["opencode"] = "opencode"
    permission: Permission = Permission.full


Provider = ClaudeCode | GeminiCLI | OpenCode

_PROVIDERS_BY_NAME: dict[str, type[_ProviderBase]] = {
    "claude-code": ClaudeCode,
    "gemini":      GeminiCLI,
    "opencode":    OpenCode,
}


class Agent(BaseModel):
    """An agent profile. Two construction shapes are supported, both
    equivalent after validation:

    Provider-object shape (preferred — per-provider fields are validated):
        Agent(provider=ClaudeCode(model="opus", effort="high"))
        Agent(provider=GeminiCLI(model="gemini-3-flash-preview"))
        Agent(provider=OpenCode(model="opencode/claude-sonnet-4-6"))

    Flat shape (legacy — still works; constructs the provider internally):
        Agent(harness="claude-code", model="opus", effort="high",
              permission="auto")
        Agent(harness="gemini",      model="gemini-3-flash-preview",
              permission="full")

    Internally a Provider object is always populated post-validation, so
    drivers / queue config / TUI can read either ``agent.provider.*`` or
    the legacy flat fields uniformly.
    """
    provider: Provider | None = None
    # Legacy flat fields — empty string default so the validator can tell
    # apart "user gave flat fields" from "user gave provider= only".
    harness: str = ""
    model: str = ""
    effort: Effort = Effort.high
    permission: Permission = Permission.auto

    @model_validator(mode="after")
    def _sync_provider_and_flat(self) -> "Agent":
        if self.provider is not None:
            # New shape: provider was given. Derive the flat fields.
            self.harness = self.provider.name
            self.model = self.provider.model
            self.permission = self.provider.permission
            self.effort = getattr(self.provider, "effort", Effort.high)
            return self
        # Legacy shape: build the provider object from the flat fields.
        if not self.harness:
            raise ValueError(
                "Agent requires either provider=<ClaudeCode|GeminiCLI"
                "|OpenCode> or the flat shape harness=+model=+...")
        klass = _PROVIDERS_BY_NAME.get(self.harness)
        if klass is None:
            raise ValueError(
                f"unknown harness {self.harness!r}; "
                f"known: {sorted(_PROVIDERS_BY_NAME)}")
        kw: dict = {"model": self.model, "permission": self.permission}
        if klass is ClaudeCode:
            kw["effort"] = self.effort
        self.provider = klass(**kw)
        return self


class ConfigError(Exception):
    pass


def default_search_paths() -> list[Path]:
    return [Path.cwd() / ".aegis.py", Path.home() / ".aegis.py"]


def find_project_root(start: Path | None = None) -> Path | None:
    """Closest ancestor of `start` (default cwd) containing .aegis.py."""
    cur = (start or Path.cwd()).resolve()
    for d in (cur, *cur.parents):
        if (d / ".aegis.py").is_file():
            return d
    return None


def load_config(
    search_paths: Sequence[Path] | None = None,
) -> tuple[dict[str, Agent], str]:
    if search_paths is None:
        root = find_project_root()
        paths = ([root / ".aegis.py"] if root else []) + [Path.home() / ".aegis.py"]
    else:
        paths = list(search_paths)
    target = next((p for p in paths if p.is_file()), None)
    if target is None:
        raise ConfigError(
            "No .aegis.py found in the current directory or home. "
            "Run `aegis init` to create one."
        )

    namespace: dict[str, object] = {}
    try:
        code = compile(target.read_text(), str(target), "exec")
        exec(code, namespace)  # noqa: S102 - config is intentionally Python
    except ValidationError as e:
        raise ConfigError(f"Invalid agent in {target} (permission/effort?): {e}") from e
    except Exception as e:  # noqa: BLE001 - surface any config error cleanly
        raise ConfigError(f"Failed to load {target}: {e}") from e

    agents = namespace.get("agents")
    default_agent = namespace.get("default_agent")
    if not isinstance(agents, dict) or not agents:
        raise ConfigError(f"{target} must define a non-empty `agents` dict.")
    for name, agent in agents.items():
        if not isinstance(agent, Agent):
            raise ConfigError(
                f"agents[{name!r}] in {target} is not an Agent instance."
            )
    if not isinstance(default_agent, str) or default_agent not in agents:
        raise ConfigError(
            f"`default_agent` in {target} must be one of {sorted(agents)}."
        )
    return agents, default_agent


def load_queues(path: Path) -> "dict[str, object]":
    """Parse the ``queues`` dict from a .aegis.py file.

    Returns ``{}`` if the file declares no queues. Raises ``ConfigError``
    on any structural or referential error, naming the offending queue
    so the operator can fix the right place.
    """
    from aegis.budget.budgets import BudgetConfigError, parse_budgets
    from aegis.queue import Queue

    namespace: dict[str, object] = {}
    try:
        exec(compile(path.read_text(), str(path), "exec"),  # noqa: S102
             namespace)
    except Exception as e:  # noqa: BLE001 — config is intentionally Python
        raise ConfigError(f"Failed to load {path}: {e}") from e

    queues_raw = namespace.get("queues")
    if queues_raw is None:
        return {}
    if not isinstance(queues_raw, dict):
        raise ConfigError(f"{path}: `queues` must be a dict.")

    agents_raw = namespace.get("agents")
    agents: dict[str, Agent] = (
        dict(agents_raw) if isinstance(agents_raw, dict) else {})
    agent_names: set[str] = set(agents)

    out: dict[str, Queue] = {}
    for name, cfg in queues_raw.items():
        if not isinstance(cfg, dict):
            raise ConfigError(
                f"{path}: queues[{name!r}] must be a dict.")
        if "agent" not in cfg:
            raise ConfigError(
                f"{path}: queues[{name!r}] missing required key 'agent'.")
        if "max_parallel" not in cfg:
            raise ConfigError(
                f"{path}: queues[{name!r}] missing required key "
                f"'max_parallel'.")
        agent_ref = cfg["agent"]
        cap = cfg["max_parallel"]
        if agent_ref not in agent_names:
            raise ConfigError(
                f"{path}: queues[{name!r}].agent={agent_ref!r} does not "
                f"reference a declared agent profile "
                f"(known: {sorted(agent_names)}).")
        if not isinstance(cap, int) or cap < 1:
            raise ConfigError(
                f"{path}: queues[{name!r}].max_parallel must be an int "
                f">= 1 (got {cap!r}).")
        agent = agents[agent_ref]
        try:
            budgets = parse_budgets(cfg.get("budgets"))
        except BudgetConfigError as e:
            raise ConfigError(f"{path}: queues[{name!r}].budgets: {e}")
        out[name] = Queue(name=name, agent_profile=agent_ref,
                          max_parallel=cap,
                          provider=agent.harness, model=agent.model,
                          budgets=budgets)
    return out
