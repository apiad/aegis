"""What an OpenCode child is started with, and its catalog. No I/O here.

OpenCode reads its config once at start, from ``OPENCODE_CONFIG_CONTENT``
merged over the user's own: the aegis MCP server with this session's token,
and the permission rules. aegis has no approval prompt, so no rule asks: the
``question`` tool and the ``doom_loop`` check both wait on a reply nobody can
send. The four permissions deny strictly less from ``read`` to ``full``, so a
session an agent spawns still has at most the agent's power.
"""

from __future__ import annotations

from typing import Any

from ..claude.control import Catalog, Model, _doc

PERMISSIONS: dict[str, dict[str, str]] = {
    "read": {
        "*": "allow",
        "edit": "deny",
        "bash": "deny",
        "task": "deny",
        "external_directory": "deny",
    },
    "write": {"*": "allow", "bash": "deny", "external_directory": "deny"},
    "auto": {"*": "allow", "external_directory": "deny"},
    "full": {"*": "allow"},
}
ALWAYS = {"aegis_*": "allow", "question": "deny", "doom_loop": "deny"}
SOURCES = {"command": "opencode", "skill": "skill", "mcp": "mcp"}


def rules(permission: str) -> dict[str, str]:
    return {**PERMISSIONS[permission], **ALWAYS}


def child_config(mcp: tuple[str, str] | None, permission: str) -> dict[str, Any]:
    cfg: dict[str, Any] = {"permission": rules(permission)}
    if mcp is not None:
        from ..mcp import HEADER

        url, token = mcp
        cfg["mcp"] = {
            "aegis": {
                "type": "remote",
                "url": url,
                "headers": {HEADER: token},
                "oauth": False,
                "enabled": True,
            }
        }
    return cfg


def split_model(model: str) -> dict[str, str]:
    provider, _, model_id = model.partition("/")
    return {"providerID": provider, "modelID": model_id}


def catalog_from(commands: list, providers: dict) -> Catalog:
    cmds = tuple(
        {
            "name": str(c["name"]),
            "hint": "",
            "doc": _doc(str(c.get("description") or "")),
            "source": SOURCES.get(str(c.get("source")), "opencode"),
        }
        for c in commands
        if isinstance(c, dict) and c.get("name")
    )
    models: list[Model] = []
    for p in providers.get("providers") or []:
        if not isinstance(p, dict) or not p.get("id"):
            continue
        for mid, m in (p.get("models") or {}).items():
            if not isinstance(m, dict):
                continue
            value = f"{p['id']}/{mid}"
            window = (m.get("limit") or {}).get("context")
            cost = m.get("cost") or {}
            caps = m.get("capabilities") or {}
            models.append(
                Model(
                    value=value,
                    resolved=value,
                    label=str(m.get("name") or value),
                    doc="",
                    efforts=tuple((m.get("variants") or {}).keys()),
                    window=window if isinstance(window, int) else None,
                    free=cost.get("input") == 0
                    and cost.get("output") == 0
                    and caps.get("toolcall") is True
                    and (caps.get("output") or {}).get("text") is True,
                )
            )
    return Catalog(cmds, tuple(models))
