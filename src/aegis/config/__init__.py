from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal, TYPE_CHECKING

from pydantic import BaseModel, model_validator

if TYPE_CHECKING:
    from aegis.queue.schema import Queue


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


@dataclass(frozen=True)
class NetworkConfig:
    """What the F3 NETWORK rows probe, and how often.

    Three cadences because the readings differ by a factor of three thousand
    in cost. `enabled` ships true: a handshake and a 300-byte lookup are
    invisible. `speed_interval` ships 0 — a repeating megabyte download from
    a speed-test host is the part that needs consent, and `/net` takes that
    reading on demand anyway.

    `anchors` is parsed at load rather than at probe time. A malformed entry
    found by the prober is a row that reads "not sampled yet" forever without
    saying why.
    """

    enabled: bool = True
    interval: float = 20.0
    trace_interval: float = 300.0
    speed_interval: float = 0.0  # 0 = off
    speed_bytes: int = 1_000_000
    timeout: float = 3.0
    # The megabyte download needs its own budget. A handshake that has not
    # answered in 3s is a dead anchor; a 1 MB body that has not arrived in
    # 3s is an ordinary slow link — and the slow link is the one the probe
    # exists to measure, so sharing `timeout` made it fail exactly where
    # the answer mattered and succeed where it did not.
    speed_timeout: float = 30.0
    anchors: tuple[tuple[str, int], ...] = (("1.1.1.1", 443), ("8.8.8.8", 443))


@dataclass(frozen=True)
class FleetConfig:
    """The F10 dashboard's paid call.

    `watched` — only sessions whose card or sidebar a client has on screen.
    `off` — never. There is no always-on mode: paying for sessions nobody
    is looking at is what the watcher gate exists to prevent, and
    `aegis dash` on a second monitor already counts as looking.
    """

    recap: str = "watched"
    recap_after_s: int = 60
    recap_interval_s: int = 120


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
    name: Literal["claude-code"] = "claude-code"
    effort: Effort = Effort.high


class GeminiCLI(_ProviderBase):
    name: Literal["gemini"] = "gemini"
    permission: Permission = Permission.full


class OpenCode(_ProviderBase):
    name: Literal["opencode"] = "opencode"
    permission: Permission = Permission.full


class Lovelaice(_ProviderBase):
    name: Literal["lovelaice"] = "lovelaice"
    permission: Permission = Permission.full
    base_url: str | None = None
    api_key_file: str | None = None


Provider = ClaudeCode | GeminiCLI | OpenCode | Lovelaice

_PROVIDERS_BY_NAME: dict[str, type[_ProviderBase]] = {
    "claude-code": ClaudeCode,
    "gemini": GeminiCLI,
    "opencode": OpenCode,
    "lovelaice": Lovelaice,
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
    prompt: str | None = None  # optional persona system-prompt file path
    # Optional default execution host — a `hosts:` key, or "local". Purely
    # aegis-side placement, so it is not a provider concern and does not
    # participate in the provider/flat sync below.
    host: str | None = None

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
                "|OpenCode> or the flat shape harness=+model=+..."
            )
        klass = _PROVIDERS_BY_NAME.get(self.harness)
        if klass is None:
            raise ValueError(
                f"unknown harness {self.harness!r}; known: {sorted(_PROVIDERS_BY_NAME)}"
            )
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
            "ancestor. Run `aegis init` to create one."
        )
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
            f"{target / '.aegis.yaml'} must declare a non-empty `agents:` section."
        )
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
                f"{target / '.aegis.yaml'}: queues[{name!r}].budgets: {e}"
            ) from e
        out[name] = Queue(
            name=name,
            agent_profile=qspec.agent,
            max_parallel=qspec.max_parallel,
            provider=agent.harness,
            model=agent.model,
            budgets=budgets,
            max_attempts=qspec.max_attempts,
            recoverable_ttl_s=qspec.recoverable_ttl_s,
        )
    return out
