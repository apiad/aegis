"""What an OpenCode child is started with, and its catalog. No I/O here.

OpenCode reads its config once at start, from ``OPENCODE_CONFIG_CONTENT``
merged over the user's own: the aegis MCP server with this session's token,
and the permission rules. aegis has no approval prompt, so no rule asks: the
``question`` tool and the ``doom_loop`` check both wait on a reply nobody can
send. The four permissions deny strictly less from ``read`` to ``full``, so a
session an agent spawns still has at most the agent's power.

Under the owner's ``knowledge: native`` (setup.py) a rule denies the
``skill`` action. In OpenCode 2.1.2 that both lists no skill in the
instructions (SkillInstructions lists the skills a ruleset does not deny)
and refuses the skill tool. OpenCode keeps no memory of its own to turn off.

OpenCode 2 runs MCP tools through "code mode" by default: the model writes a
script that calls them, and no ``aegis_*`` call reaches the stream. aegis turns
it off for its own server, so its tools are called, and shown, by name.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..claude.control import Catalog, Model, _doc

PERMISSIONS: dict[str, dict[str, str]] = {
    "read": {
        "*": "allow",
        "edit": "deny",
        "write": "deny",
        "shell": "deny",
        "subagent": "deny",
        "external_directory": "deny",
    },
    "write": {"*": "allow", "shell": "deny", "external_directory": "deny"},
    "auto": {"*": "allow", "external_directory": "deny"},
    "full": {"*": "allow"},
}
ALWAYS = {"aegis_*": "allow", "question": "deny", "doom_loop": "deny"}
NATIVE = {"skill": "deny"}


def rules(
    permission: str, read_dirs: tuple[Path, ...] = (), native: bool = False
) -> dict[str, Any]:
    """Action -> effect, or for a path rule resource -> effect."""
    out: dict[str, Any] = {
        **PERMISSIONS[permission],
        **ALWAYS,
        **(NATIVE if native else {}),
    }
    if read_dirs and out.get("external_directory") == "deny":
        # The inbox lives outside the cwd; reading it must not trip the deny.
        # OpenCode applies the last matching rule, so "*" goes first.
        out["external_directory"] = {
            "*": "deny",
            **{f"{folder}/*": "allow" for folder in read_dirs},
        }
    return out


def ruleset(
    permission: str, read_dirs: tuple[Path, ...] = (), native: bool = False
) -> list[dict]:
    """The rules as OpenCode 2 lists them, in the order they apply."""
    out = []
    for action, effect in rules(permission, read_dirs, native).items():
        by_resource = effect if isinstance(effect, dict) else {"*": effect}
        for resource, e in by_resource.items():
            out.append({"action": action, "resource": resource, "effect": e})
    return out


def child_config(
    mcp: tuple[str, str] | None,
    permission: str,
    read_dirs: tuple[Path, ...] = (),
    native: bool = False,
) -> dict[str, Any]:
    cfg: dict[str, Any] = {"permissions": ruleset(permission, read_dirs, native)}
    if mcp is not None:
        from ..mcp import HEADER

        url, token = mcp
        cfg["mcp"] = {
            "servers": {
                "aegis": {
                    "type": "remote",
                    "url": url,
                    "headers": {HEADER: token},
                    "oauth": False,
                    "codemode": False,
                }
            }
        }
    return cfg


def split_model(model: str) -> dict[str, str]:
    provider, _, model_id = model.partition("/")
    return {"providerID": provider, "id": model_id}


def _price(v: object) -> float | None:
    """A cost from the catalog, USD per million tokens, or None when absent."""
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def catalog_from(commands: list, skills: list, models: list) -> Catalog:
    cmds = tuple(
        {
            "name": str(c["name"]),
            "hint": "",
            "doc": _doc(str(c.get("description") or "")),
            "source": source,
        }
        for source, key, items in (
            ("opencode", "name", commands),
            ("skill", "id", skills),
        )
        for c in ({**c, "name": c.get(key)} for c in items if isinstance(c, dict))
        if c.get("name")
    )
    out: list[Model] = []
    for m in models:
        if not isinstance(m, dict) or not m.get("providerID") or not m.get("id"):
            continue
        if m.get("enabled") is False:
            continue
        value = f"{m['providerID']}/{m['id']}"
        window = (m.get("limit") or {}).get("context")
        costs = m.get("cost") or [{}]
        cost = costs[0] if isinstance(costs, list) and costs else {}
        caps = m.get("capabilities") or {}
        out.append(
            Model(
                value=value,
                resolved=value,
                label=str(m.get("name") or value),
                doc="",
                efforts=tuple(
                    str(v["id"])
                    for v in m.get("variants") or []
                    if isinstance(v, dict) and v.get("id")
                ),
                window=window if isinstance(window, int) else None,
                price_in=_price(cost.get("input")),
                price_out=_price(cost.get("output")),
                free=cost.get("input") == 0
                and cost.get("output") == 0
                and caps.get("tools") is True
                and "text" in (caps.get("output") or []),
            )
        )
    return Catalog(cmds, tuple(out))
