from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal, TYPE_CHECKING

from pydantic import BaseModel, model_validator

if TYPE_CHECKING:
    from aegis.queue.schema import Queue


DEFAULT_TELEGRAM_PROMPT = (
    "You are replying via telegram, keep response compact and focused "
    "if possible, only resort to long responses if it really matters")


@dataclass(frozen=True)
class TelegramConfig:
    token: str | None
    chat_id: int | None
    auto_prompt: str


@dataclass(frozen=True)
class WebConfig:
    token: str | None = None
    bind: str = "127.0.0.1"
    port: int | None = None


@dataclass(frozen=True)
class VoiceConfig:
    enabled: bool = False
    model: str = "base"
    key: str = "ctrl+g"
    preview: bool = False
    language: str | None = None


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
    harness CLI (claude-code, gemini, opencode, copilot) and carry the
    per-provider fields that matter for that CLI."""
    model: str
    permission: Permission = Permission.auto


class ClaudeCode(_ProviderBase):
    name: Literal["claude-code"] = "claude-code"
    effort: Effort = Effort.high


class GeminiCLI(_ProviderBase):
    name: Literal["gemini"] = "gemini"
    permission: Permission = Permission.full


class OpenCode(_ProviderBase):
    name: Literal["opencode"] = "opencode"
    permission: Permission = Permission.full


class CopilotCLI(_ProviderBase):
    name: Literal["copilot"] = "copilot"
    permission: Permission = Permission.full


Provider = ClaudeCode | GeminiCLI | OpenCode | CopilotCLI

_PROVIDERS_BY_NAME: dict[str, type[_ProviderBase]] = {
    "claude-code": ClaudeCode,
    "gemini":      GeminiCLI,
    "opencode":    OpenCode,
    "copilot":     CopilotCLI,
}


class Agent(BaseModel):
    """An agent profile.

    Provider-object shape (preferred):
        Agent(provider=ClaudeCode(model="opus", effort="high"))

    Flat shape (legacy):
        Agent(harness="claude-code", model="opus", effort="high",
              permission="auto")
    """
    provider: Provider | None = None
    harness: str = ""
    model: str = ""
    effort: Effort = Effort.high
    permission: Permission = Permission.auto

    @model_validator(mode="after")
    def _sync_provider_and_flat(self) -> "Agent":
        if self.provider is not None:
            self.harness = self.provider.name
            self.model = self.provider.model
            self.permission = self.provider.permission
            self.effort = getattr(self.provider, "effort", Effort.high)
            return self
        if not self.harness:
            raise ValueError(
                "Agent requires either provider=<ClaudeCode|GeminiCLI"
                "|OpenCode|CopilotCLI> or the flat shape harness=+model=+...")
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


# --- YAML-backed loaders -------------------------------------------------
#
# .aegis.yaml is the only config substrate. The legacy .aegis.py path
# was removed in the migration to declarative YAML.


def find_project_root(start: Path | None = None) -> Path | None:
    """Closest ancestor of `start` (default cwd) containing .aegis.yaml."""
    cur = (start or Path.cwd()).resolve()
    for d in (cur, *cur.parents):
        if (d / ".aegis.yaml").is_file():
            return d
    return None


def _resolve_root(root: Path | None) -> Path:
    if root is not None:
        return root
    found = find_project_root()
    if found is None:
        raise ConfigError(
            "No .aegis.yaml found in the current directory or any "
            "ancestor. Run `aegis init` to create one.")
    return found


def load_config(
    root: Path | None = None,
) -> tuple[dict[str, Agent], str]:
    """Load agents + default_agent from .aegis.yaml at `root` (or the
    discovered project root). Plain-tuple return for back-compat with
    pre-YAML call sites."""
    from aegis.config.yaml_loader import load_config as _load_yaml
    target = _resolve_root(root)
    cfg = _load_yaml(target)
    if not cfg.agents:
        raise ConfigError(
            f"{target / '.aegis.yaml'} must declare a non-empty "
            f"`agents:` section.")
    assert cfg.default_agent is not None  # validated in yaml_loader
    return cfg.agents, cfg.default_agent


def load_queues(root: Path | None = None) -> "dict[str, Queue]":
    """Load + validate queues from .aegis.yaml. Builds full Queue
    objects with budgets resolved and agent provider/model copied in."""
    from aegis.budget.budgets import BudgetConfigError, parse_budgets
    from aegis.config.yaml_loader import load_config as _load_yaml
    from aegis.queue.schema import Queue

    target = _resolve_root(root)
    cfg = _load_yaml(target)
    out: dict[str, Queue] = {}
    for name, qspec in cfg.queues.items():
        agent = cfg.agents[qspec.agent]
        try:
            budgets = parse_budgets(qspec.budgets)
        except BudgetConfigError as e:
            raise ConfigError(
                f"{target / '.aegis.yaml'}: queues[{name!r}].budgets: "
                f"{e}") from e
        out[name] = Queue(
            name=name,
            agent_profile=qspec.agent,
            max_parallel=qspec.max_parallel,
            provider=agent.harness,
            model=agent.model,
            budgets=budgets,
        )
    return out


def load_telegram_config(root: Path | None = None) -> TelegramConfig:
    """Load the telegram block from .aegis.yaml. Returns a TelegramConfig
    with token=None/chat_id=None when no telegram section is declared."""
    from aegis.config.yaml_loader import load_config as _load_yaml
    target = _resolve_root(root)
    cfg = _load_yaml(target)
    assert cfg.telegram is not None  # always built by yaml_loader
    return cfg.telegram
