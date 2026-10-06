"""Agent profiles, read from the ``agents:`` map of the old ``.aegis.yaml``.

This is the only part of the old config aegis2 reads. Both the flat form
(``model:``, ``effort:``, ``permission:``, ``harness:``) and the nested
``provider:`` form are accepted. Defaults match the old tree: harness
``claude-code``, effort ``high``, permission ``auto``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ruamel.yaml import YAML, YAMLError

from .roots import CONFIG_FILE

# The old tree's permission vocabulary, mapped to Claude Code's modes.
PERMISSION_MODE = {
    "read": "plan",
    "write": "acceptEdits",
    "full": "bypassPermissions",
    "auto": "auto",
}
EFFORTS = ("low", "medium", "high", "max")
SUPPORTED_HARNESSES = ("claude-code",)


class ProfileError(Exception):
    pass


@dataclass(frozen=True)
class Profile:
    name: str
    harness: str
    model: str
    effort: str
    permission: str

    @property
    def enabled(self) -> bool:
        return self.harness in SUPPORTED_HARNESSES

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "harness": self.harness,
            "model": self.model,
            "effort": self.effort,
            "permission": self.permission,
            "enabled": self.enabled,
        }


def _profile(name: str, raw: object) -> Profile:
    d = raw if isinstance(raw, dict) else {}
    provider = d.get("provider")
    if isinstance(provider, dict):
        d = {**d, **provider, "harness": provider.get("name", d.get("harness"))}
    return Profile(
        name=str(name),
        harness=str(d.get("harness") or "claude-code"),
        model=str(d.get("model") or ""),
        effort=str(d.get("effort") or "high"),
        permission=str(d.get("permission") or "auto"),
    )


def _config(config_root: Path) -> dict:
    path = config_root / CONFIG_FILE
    if not path.is_file():
        return {}
    try:
        data = YAML(typ="safe").load(path.read_text())
    except YAMLError as e:
        raise ProfileError(f"{path}: {e}") from e
    return data if isinstance(data, dict) else {}


def default_profile(config_root: Path) -> str | None:
    name = _config(config_root).get("default_agent")
    return str(name) if name else None


def load_profiles(config_root: Path) -> list[Profile]:
    agents = _config(config_root).get("agents")
    if not isinstance(agents, dict):
        return []
    return [_profile(name, raw) for name, raw in agents.items()]
