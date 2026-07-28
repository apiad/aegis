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

from aegis.config import Permission

_DRIVERS = ("claude-code", "gemini", "opencode", "lovelaice")


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
