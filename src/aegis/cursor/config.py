"""What a Cursor child is started with, and its catalog. No I/O here.

Measured on cursor-agent 2026.10.14 (spec
``2026-10-19-aegis-2-cursor-harness-design.md``):

- The child is ``cursor-agent acp --trust``: an ACP server on stdio, one
  process per session, its ``initialize`` answering in about 8 s.
- The token rides in ``session/new``'s ``mcpServers`` as a header: Cursor reads
  MCP only from ``.cursor/mcp.json`` and ``~/.cursor/mcp.json``, which
  ``CURSOR_CONFIG_DIR`` does not move (``mcp list`` still points at the fixed
  paths), so ACP is the only door a per-session server fits through.
- Permissions are a mode plus ``session/request_permission``: the server sends
  it for a command outside its allowlist (measured: ``Not in allowlist: cat``)
  with ``allow_once`` / ``allow_always`` / ``reject_once`` options. aegis
  answers at its own level, mirroring ``opencode/config.py PERMISSIONS`` where
  ``write`` allows files and denies ``shell``. ``allow_always`` is never
  chosen: it would outlive the session in Cursor's own config.
- ``session/set_model`` takes a modelId from the session's own catalog and
  nothing else: a plain name (``claude-opus-5``) or the same id with another
  ``effort=`` is ``-32602 Invalid model value``. The effort therefore lives
  inside the id, and an effort change can only confirm what the model already
  carries, else it is refused (process.ControlError).
- A second ``session/prompt`` while one runs cancels the running turn (its
  ``stopReason`` becomes ``cancelled``), so a prompt typed mid-turn is queued
  in the process and read when the turn ends.
"""

from __future__ import annotations

import re
from typing import Any

from ..claude.control import Catalog, Model, _doc

SERVER = "aegis"
PERMISSIONS = ("read", "write", "auto", "full")
# aegis's level -> Cursor's mode. Only read loses power (plan is read-only by
# construction); write, auto and full all run as agent, each with its own
# answer to a permission request below.
MODE = {"read": "plan", "write": "agent", "auto": "agent", "full": "agent"}
# The kinds a tool call is reported with, as Cursor names them (its schema:
# read, edit, delete, move, search, execute, ...), and the row each becomes.
KIND_NAMES = {
    "read": "Read",
    "edit": "Edit",
    "delete": "Edit",
    "move": "Edit",
    "search": "Grep",
    "execute": "Bash",
    "fetch": "WebFetch",
}
# The effort-like keys a model id's bracket overrides carry, in the order the
# catalog shows them (``effort``, ``reasoning_effort``, ``reasoning``).
_EFFORT = re.compile(r"(?:reasoning_effort|effort|reasoning)=([^,\]]+)")


def argv(bin: str) -> list[str]:
    """The child's command line: ``--trust`` so no workspace-trust prompt can
    stop a start (measured: acp accepts it, and rejects unknown flags)."""
    return [bin, "acp", "--trust"]


def mcp_servers(mcp: tuple[str, str] | None) -> list[dict]:
    """What ``session/new`` and ``session/load`` get as ``mcpServers``: the
    aegis endpoint, with this session's token as a header, or nothing."""
    if mcp is None:
        return []
    url, token = mcp
    return [
        {
            "name": SERVER,
            "type": "http",
            "url": url,
            "headers": [{"name": "X-Aegis-Session", "value": token}],
        }
    ]


def mode_for(permission: str) -> str:
    return MODE.get(permission, "agent")


def allows(permission: str, kind: str) -> bool:
    """Whether a level answers a permission request with ``allow_once``.

    Only a shell command (``execute``) is gated between write and auto, which
    is the line ``opencode/config.py PERMISSIONS`` draws; a level's other
    requests (a file edit outside the allowlist, a fetch) are allowed.
    """
    if permission in ("auto", "full"):
        return True
    if permission == "write":
        return kind != "execute"
    return False


def effort_of(model_id: str) -> str | None:
    """The effort a model id carries in its bracket, or None."""
    m = _EFFORT.search(model_id.partition("[")[2])
    return m.group(1) if m else None


def models_from(entries: list, current: str = "") -> tuple[Model, ...]:
    """The catalog ``session/new`` answers with, one model per entry.

    ``value`` is the id's own short name (``claude-opus-5``, ``default`` for
    ``default[]``), which is unique across a catalog (measured on the 46), and
    what the person writes in ``.aegis.yaml``; ``label`` is the name Cursor
    shows (``Auto``); ``resolved`` the exact modelId ``session/set_model``
    accepts; and ``efforts`` the single effort the id already carries.
    """
    models: list[Model] = []
    for m in entries:
        if not isinstance(m, dict) or not m.get("modelId"):
            continue
        model_id = str(m["modelId"])
        short = model_id.partition("[")[0]
        label = str(m.get("name") or short)
        effort = effort_of(model_id)
        models.append(
            Model(
                value=short,
                resolved=model_id,
                label=label,
                doc=_doc_of(m),
                efforts=(effort,) if effort else (),
            )
        )
    if current and not any(current in (m.value, m.resolved) for m in models):
        models.append(
            Model(
                value=current,
                resolved=current,
                label=current,
                doc="",
                efforts=(effort_of(current),) if effort_of(current) else (),
            )
        )
    return tuple(models)


def _doc_of(m: dict) -> str:
    return _doc(str(m.get("description") or ""))


def catalog_from(models: list[Model], commands: list[dict]) -> Catalog:
    return Catalog(tuple(commands), tuple(models))


def command_list(entries: list) -> list[dict]:
    """``available_commands_update`` entries as the catalog's commands."""
    out: list[dict] = []
    for c in entries:
        if isinstance(c, dict) and c.get("name"):
            out.append(
                {
                    "name": str(c["name"]),
                    "hint": "",
                    "doc": str(c.get("description") or ""),
                    "source": "cursor",
                }
            )
    return out


def tool_name(kind: str, title: str, raw_input: dict[str, Any]) -> tuple[str, dict]:
    """The aegis tool row a Cursor call becomes, as ``(name, input)``.

    The title of an MCP call names its server (``aegis: meta``), so it becomes
    the prefixed name the fold strips again; a file tool's ``path`` becomes the
    ``file_path`` every view in ``transcript/describe.py`` reads.
    """
    if title.startswith(f"{SERVER}: "):
        name = f"mcp__{SERVER}__{title.partition(': ')[2]}"
    else:
        name = KIND_NAMES.get(kind) or title.strip() or "Tool"
    if "path" in raw_input and "file_path" not in raw_input:
        raw_input = {**raw_input, "file_path": raw_input["path"]}
    return name, raw_input
