"""What matters about each aegis MCP call.

One registry, two consumers. The transcript renderers ask for a glyph and a
one-line description; the comms middleware asks for a family and a target.
Keeping both answers in one place is the point: if they disagreed about who
the counterpart is, the ledger would contradict the screen.

Pure by contract — no Rich, no Textual, no I/O, no bridge. ``render_shared``
imports this, and the web wire imports ``render_shared``.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

CONVERSATION = "conversation"
COORDINATION = "coordination"
INTROSPECTION = "introspection"
ADMIN = "admin"

#: The one glyph every read-the-room call shares. Rendered dimmer than a
#: native tool line, so polling never wears the conversation's colour.
PALE_GLYPH = "∘"

_PREFIX = "aegis_"


@dataclass(frozen=True)
class Target:
    """Who or what is on the other end of a call.

    ``kind`` is one of: agent, queue, canvas, group, term, path, claim, self.
    """
    kind: str
    id: str


@dataclass(frozen=True)
class AegisToolDescriptor:
    verb: str
    family: str
    #: A constant glyph, or a function of the arguments when the same tool
    #: means two different acts (``handoff`` with and without ``interrupt``).
    glyph: str | Callable[[dict], str]
    describe: Callable[[dict], str]
    target: Callable[[dict], Target | None] = field(
        default=lambda args: None)


# --- argument helpers -------------------------------------------------

def _s(args: dict, *keys: str) -> str:
    """The first non-empty string among ``keys``. Never raises: the renderer
    sees whatever the model actually sent, which may be nothing."""
    for key in keys:
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _quote(text: str, limit: int = 48) -> str:
    """A whitespace-collapsed, truncated, quoted excerpt — or "" if empty."""
    collapsed = " ".join(str(text or "").split())
    if not collapsed:
        return ""
    if len(collapsed) > limit:
        collapsed = collapsed[:limit - 1] + "…"
    return f'"{collapsed}"'


def _join(*parts: str) -> str:
    """Join non-empty segments with the house separator. Dropping empties is
    what keeps a call with missing arguments from rendering as ' · · '."""
    return " · ".join(p for p in parts if p)


def _at(name: str, host: str) -> str:
    return f"{name}@{host}" if name and host else name


def _agent_at(key: str) -> Callable[[dict], Target | None]:
    def target(args: dict) -> Target | None:
        val = _s(args, key)
        return Target("agent", val) if val else None
    return target


def _target_at(kind: str, *keys: str) -> Callable[[dict], Target | None]:
    def target(args: dict) -> Target | None:
        val = _s(args, *keys)
        return Target(kind, val) if val else None
    return target


# --- descriptions -----------------------------------------------------

def _d_handoff(a: dict) -> str:
    return _join(_s(a, "target_handle"),
                 "cut" if a.get("interrupt") else "",
                 _quote(_s(a, "context")))


def _g_handoff(a: dict) -> str:
    return "⇅" if a.get("interrupt") else "⇄"


def _d_spawn(a: dict) -> str:
    return _join(_at(_s(a, "agent"), _s(a, "host")), _quote(_s(a, "prompt")))


def _d_fork(a: dict) -> str:
    return _join(_s(a, "target_handle"), "forked", _quote(_s(a, "prompt")))


def _d_close(a: dict) -> str:
    return _join(_s(a, "handle"), "reaped")


def _d_enqueue(a: dict) -> str:
    return _join(_s(a, "queue"), _quote(_s(a, "payload")))


def _d_delegate(a: dict) -> str:
    return _join(_s(a, "queue"), "blocking", _quote(_s(a, "payload")))


def _d_cancel(a: dict) -> str:
    return _s(a, "task_id")


def _lines(text: str) -> int:
    return len(str(text or "").splitlines())


def _d_canvas_write(a: dict) -> str:
    n = _lines(_s(a, "content"))
    return _join(f"{_s(a, 'name')} §{_s(a, 'section')}", f"{n} lines")


def _d_canvas_append(a: dict) -> str:
    n = _lines(_s(a, "text"))
    return _join(f"{_s(a, 'name')} §{_s(a, 'section')}", f"+{n} lines")


def _d_canvas_subscribe(a: dict) -> str:
    sections = a.get("sections")
    which = ", ".join(sections) if sections else "all sections"
    return _join(_s(a, "name"), which)


def _d_canvas_open(a: dict) -> str:
    return _join(_s(a, "name"), _s(a, "file"))


def _d_canvas_unsubscribe(a: dict) -> str:
    return _join(_s(a, "name"), "unsubscribed")


#: The control bytes worth naming. Anything else is quoted with newlines
#: shown as ⏎ — a raw \n in a transcript line would break the row.
_CONTROL = {"\x03": "^C", "\x04": "^D", "\x1a": "^Z", "\x1b": "ESC"}


def _d_term_keys(a: dict) -> str:
    keys = a.get("keys")
    keys = keys if isinstance(keys, str) else ""
    named = _CONTROL.get(keys)
    if named:
        return _join(_s(a, "name"), named)
    return _join(_s(a, "name"), _quote(keys.replace("\n", "⏎"), 24))


def _d_term_run(a: dict) -> str:
    return _join(_s(a, "name"), _quote(_s(a, "cmd")))


def _d_term_spawn(a: dict) -> str:
    return _join(_s(a, "name"), _s(a, "cwd"))


def _d_group_wait(mode: str) -> Callable[[dict], str]:
    def describe(a: dict) -> str:
        return _join(_s(a, "group"), mode,
                     _s(a, "reducer") if mode == "all" else "")
    return describe


def _d_group_spawn(a: dict) -> str:
    return _join(_s(a, "group"), _s(a, "profile"))


def _d_group_spawn_mixed(a: dict) -> str:
    profiles = a.get("profiles") or []
    which = ", ".join(profiles) if profiles else _s(a, "preset")
    return _join(_s(a, "group"), which)


def _d_group_rename(a: dict) -> str:
    return _join(_s(a, "old"),
                 f"renamed to {_s(a, 'new')}" if _s(a, "new") else "")


def _d_group_move(a: dict) -> str:
    hop = f"{_s(a, 'from_group')} to {_s(a, 'to_group')}"
    return _join(_s(a, "handle"), hop if _s(a, "to_group") else "")


DESCRIPTORS: dict[str, AegisToolDescriptor] = {
    "handoff": AegisToolDescriptor(
        "handoff", CONVERSATION, _g_handoff, _d_handoff,
        _agent_at("target_handle")),
    "spawn": AegisToolDescriptor(
        "spawn", CONVERSATION, "✧", _d_spawn, _agent_at("agent")),
    "fork": AegisToolDescriptor(
        "fork", CONVERSATION, "✧", _d_fork, _agent_at("target_handle")),
    "close": AegisToolDescriptor(
        "close", CONVERSATION, "✦", _d_close, _agent_at("handle")),
    "enqueue": AegisToolDescriptor(
        "enqueue", CONVERSATION, "⇉", _d_enqueue,
        _target_at("queue", "queue")),
    "delegate": AegisToolDescriptor(
        "delegate", CONVERSATION, "⇉", _d_delegate,
        _target_at("queue", "queue")),
    "cancel": AegisToolDescriptor(
        "cancel", CONVERSATION, "⇎", _d_cancel,
        _target_at("queue", "task_id")),
    # --- shared surfaces ---
    "canvas_open": AegisToolDescriptor(
        "canvas_open", CONVERSATION, "▥", _d_canvas_open,
        _target_at("canvas", "name")),
    "canvas_write_section": AegisToolDescriptor(
        "canvas_write_section", CONVERSATION, "▤", _d_canvas_write,
        _target_at("canvas", "name")),
    "canvas_append_to_section": AegisToolDescriptor(
        "canvas_append_to_section", CONVERSATION, "▤", _d_canvas_append,
        _target_at("canvas", "name")),
    "canvas_subscribe": AegisToolDescriptor(
        "canvas_subscribe", CONVERSATION, "▥", _d_canvas_subscribe,
        _target_at("canvas", "name")),
    "canvas_unsubscribe": AegisToolDescriptor(
        "canvas_unsubscribe", CONVERSATION, "▧", _d_canvas_unsubscribe,
        _target_at("canvas", "name")),
    "term_spawn": AegisToolDescriptor(
        "term_spawn", CONVERSATION, "▥", _d_term_spawn,
        _target_at("term", "name")),
    "term_run": AegisToolDescriptor(
        "term_run", CONVERSATION, "■", _d_term_run,
        _target_at("term", "name")),
    "term_keys": AegisToolDescriptor(
        "term_keys", CONVERSATION, "■", _d_term_keys,
        _target_at("term", "name")),
    "term_subscribe": AegisToolDescriptor(
        "term_subscribe", CONVERSATION, "▥",
        lambda a: _join(_s(a, "name"), "subscribed"),
        _target_at("term", "name")),
    "term_unsubscribe": AegisToolDescriptor(
        "term_unsubscribe", CONVERSATION, "▧",
        lambda a: _join(_s(a, "name"), "unsubscribed"),
        _target_at("term", "name")),
    "term_close": AegisToolDescriptor(
        "term_close", CONVERSATION, "▧",
        lambda a: _join(_s(a, "name"), "closed"),
        _target_at("term", "name")),
    # --- groups ---
    "group_spawn": AegisToolDescriptor(
        "group_spawn", CONVERSATION, "✧", _d_group_spawn,
        _target_at("group", "group")),
    "group_spawn_mixed": AegisToolDescriptor(
        "group_spawn_mixed", CONVERSATION, "✧", _d_group_spawn_mixed,
        _target_at("group", "group")),
    "group_broadcast": AegisToolDescriptor(
        "group_broadcast", CONVERSATION, "⁂",
        lambda a: _join(_s(a, "group"), _quote(_s(a, "objective"))),
        _target_at("group", "group")),
    "group_wait_all": AegisToolDescriptor(
        "group_wait_all", CONVERSATION, "⁑", _d_group_wait("all"),
        _target_at("group", "group")),
    "group_wait_any": AegisToolDescriptor(
        "group_wait_any", CONVERSATION, "⁑", _d_group_wait("any"),
        _target_at("group", "group")),
    "group_rename": AegisToolDescriptor(
        "group_rename", COORDINATION, "⌗", _d_group_rename,
        _target_at("group", "old")),
    "group_dissolve": AegisToolDescriptor(
        "group_dissolve", COORDINATION, "⌗",
        lambda a: _join(_s(a, "group"), "dissolved"),
        _target_at("group", "group")),
    "group_move_member": AegisToolDescriptor(
        "group_move_member", COORDINATION, "⌗", _d_group_move,
        _target_at("group", "to_group")),
}


# --- lookup -----------------------------------------------------------

def _bare_verb(name: str) -> str:
    """``mcp__aegis__aegis_handoff`` and ``aegis_handoff`` both give
    ``handoff``.

    The two consumers see two different names: the renderer gets whatever the
    harness called the tool (claude prefixes ``mcp__<server>__``), while the
    middleware gets the bare registered name. Both land here.
    """
    if name.startswith("mcp__"):
        parts = name.split("__")
        if len(parts) >= 3:
            name = parts[-1]
    return name[len(_PREFIX):] if name.startswith(_PREFIX) else ""


def descriptor_for(name: str) -> AegisToolDescriptor | None:
    return DESCRIPTORS.get(_bare_verb(name))


def aegis_glyph(name: str, args: dict | None = None) -> str | None:
    d = descriptor_for(name)
    if d is None:
        return None
    return d.glyph(args or {}) if callable(d.glyph) else d.glyph


def aegis_describe(name: str, args: dict | None = None) -> str | None:
    d = descriptor_for(name)
    return None if d is None else d.describe(args or {})


def aegis_target(name: str, args: dict | None = None) -> Target | None:
    d = descriptor_for(name)
    return None if d is None else d.target(args or {})


def aegis_family(name: str) -> str | None:
    d = descriptor_for(name)
    return None if d is None else d.family
