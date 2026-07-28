"""Harness registry — named provider entries (driver + credentials).

A `HarnessRegistration` elevates today's per-agent provider fields
(`base_url` / `api_key_file`) into a named, top-level `.aegis.yaml`
`harnesses:` entry. Agents reference a harness by name; the four driver
strings auto-register as implicit harnesses so legacy configs keep working.

Resolution rewrites an agent's `harness` to the underlying **driver string**
so every `get_driver(agent.harness)` call site works unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass

from aegis.config import (
    Agent,
    ClaudeCode,
    ConfigError,
    GeminiCLI,
    Lovelaice,
    OpenCode,
    Permission,
)

_DRIVERS = ("claude-code", "gemini", "opencode", "lovelaice")

_PROVIDER_BY_DRIVER: dict[str, type] = {
    "claude-code": ClaudeCode,
    "gemini": GeminiCLI,
    "opencode": OpenCode,
    "lovelaice": Lovelaice,
}


@dataclass(frozen=True)
class HarnessRegistration:
    name: str
    driver: str
    base_url: str | None = None
    api_key_file: str | None = None
    default_model: str | None = None
    permission_default: Permission | None = None


IMPLICIT_HARNESSES: dict[str, HarnessRegistration] = {
    d: HarnessRegistration(name=d, driver=d) for d in _DRIVERS
}


def merge_harnesses(
    explicit: dict[str, HarnessRegistration],
) -> dict[str, HarnessRegistration]:
    """Implicit driver-name registrations + explicit ones; explicit wins."""
    return {**IMPLICIT_HARNESSES, **explicit}


def resolve_agent_entry(
    body: dict, harnesses: dict[str, HarnessRegistration],
) -> Agent:
    """Build a fully-resolved `Agent` from a raw agent YAML mapping.

    The mapping carries **either** `harness: <registry-key>` (new shape) or
    the legacy `provider: <driver>` (a driver name is also a valid harness
    key via the implicit registrations). The returned Agent's `.harness` is
    the underlying **driver string** — so `get_driver(agent.harness)` works —
    with credentials copied from the harness registration and `.prompt` set
    from the mapping's `prompt` key.
    """
    d = dict(body)
    prompt = d.pop("prompt", None)
    reg_key = d.pop("harness", None)
    if reg_key is None:
        provider_name = d.pop("provider", None)
        if provider_name is None:
            raise ConfigError(
                f"agent missing `harness` or `provider`: {body!r}")
        reg_key = provider_name          # provider name IS a driver name
    reg = harnesses.get(reg_key)
    if reg is None:
        raise ConfigError(
            f"unknown harness {reg_key!r}; known: {sorted(harnesses)}")
    cls = _PROVIDER_BY_DRIVER[reg.driver]
    model = d.get("model") or reg.default_model
    if not model:
        raise ConfigError(
            f"agent on harness {reg_key!r} has no model and the harness "
            f"declares no default_model")
    kw: dict = {"model": model}
    perm = d.get("permission") or (
        reg.permission_default.value if reg.permission_default else None)
    if perm is not None:
        kw["permission"] = perm
    if cls is ClaudeCode and d.get("effort") is not None:
        kw["effort"] = d["effort"]
    if cls is Lovelaice:
        if reg.base_url is not None:
            kw["base_url"] = reg.base_url
        if reg.api_key_file is not None:
            kw["api_key_file"] = reg.api_key_file
    return Agent(provider=cls(**kw), prompt=prompt)
