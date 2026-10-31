"""``.aegis.yaml``: the one place aegis's configuration lives.

The server holds a parsed copy (``Snapshot``) and nothing else. ``Config.current``
stats the file and re-reads it when its (mtime_ns, size, inode) changed, so every
reader sees an edit made anywhere, by the Settings page or a text editor, with no
restart; ``watch`` calls it once a second so a change is published even when
nobody reads. A file that does not parse, or is empty, leaves the last one that
did in force, with the error in the snapshot: a half-typed edit, or an editor
that truncates before it writes, must not stop every spawn or fail every queued
task. A deleted file is honoured: no agents, no queues.

``write`` is the one writer, for ``aegis init`` and the Settings page. It
refuses a stale write (the file changed since the caller read it), validates
first and writes nothing on a problem, and edits the document in place in
ruamel's round-trip mode, so comments, key order, each agent's form
(``harness:``, ``provider:`` or a nested ``provider:`` map) and keys aegis does
not read survive.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from io import StringIO
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from ruamel.yaml import YAML, YAMLError
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.scalarstring import LiteralScalarString
from ruamel.yaml.util import load_yaml_guess_indent

from .agents import EFFORTS, HARNESSES, PERMISSION_ORDER, Agent, agents_from
from .claude.process import REPLACED_TOOLS

# Imported for their notice sources' declarations (notice_sources.py), so
# the first parse of notices: knows them.
from . import monitors as _monitors  # noqa: F401
from . import nudges as _nudges  # noqa: F401
from . import notice_sources
from .cgroups import DEFAULT_MEMORY_MAX, parse_size
from .net.config import KEYS as NET_KEYS
from .host import host_from
from .net.config import NetConfig, network_from
from .ops import OpError
from .queues import queues_from
from .roots import CONFIG_FILE
from .term_profiles import RESTARTS, terminals_from
from .users import MultiUser, multi_user_from

log = logging.getLogger("aegis.config")

WATCH_EVERY_S = 1.0
TOP_KEYS = (
    "agents", "queues", "default_agent", "recap", "btw", "context", "usage",
    "session_memory_max", "terminals", "terminal_memory_max", "park", "network",
    "host", "workflows", "harness_versions", "notices", "nudges", "multi_user",
    "knowledge", "exit_via",
)  # fmt: skip
AGENT_KEYS = (
    "harness", "provider", "model", "models", "effort", "permission", "priming",
    "keep_harness_tools", "exit",
)  # fmt: skip
QUEUE_KEYS = ("agent", "max_parallel", "quiet")

Stamp = tuple[int, int, int]

# When turn_end names the context size, and when the client warns that the
# prompt cache has gone cold (cache.py, #354).
CONTEXT_DEFAULTS = {"handoff_at": 300_000, "cold_after": 3600, "cold_min": 100_000}


def context_from(raw: object) -> dict:
    """The ``context:`` block over its defaults. A wrong block keeps every
    default in force and carries ``error`` for aegis doctor, so a typo never
    stops a turn_end."""
    out: dict = dict(CONTEXT_DEFAULTS)
    if raw is None:
        return out
    if not isinstance(raw, dict):
        return out | {
            "error": "context must be a mapping of handoff_at, cold_after, cold_min"
        }
    for k, v in raw.items():
        if k not in CONTEXT_DEFAULTS:
            return dict(CONTEXT_DEFAULTS) | {
                "error": f"context has no key {k!r}; it takes handoff_at, cold_after, cold_min"
            }
        positive = k == "cold_after"
        if isinstance(v, bool) or not isinstance(v, int) or v < (1 if positive else 0):
            kind = "positive" if positive else "non-negative"
            return dict(CONTEXT_DEFAULTS) | {
                "error": f"context.{k} {v!r} is not a {kind} integer"
            }
        out[k] = v
    return out


# Who owns a local repo, how an owner is shown, and the subscriptions' fees
# (usage/owners.py, spec #737).
USAGE_DEFAULTS: dict = {"owners": {}, "owner_names": {}, "subscriptions": {}}


def _usage_defaults(error: str | None = None) -> dict:
    out: dict = {k: {} for k in USAGE_DEFAULTS}
    return out | {"error": error} if error else out


def usage_from(raw: object) -> dict:
    """The ``usage:`` block over its defaults: ``owners`` and ``owner_names``
    map a name to a name, ``subscriptions`` an account to its name, fee and
    renewal day. A wrong block keeps every default and carries ``error``."""
    out = _usage_defaults()
    if raw is None:
        return out
    if not isinstance(raw, dict):
        return _usage_defaults(
            "usage must be a mapping of " + ", ".join(USAGE_DEFAULTS)
        )
    for k, v in raw.items():
        if k not in USAGE_DEFAULTS:
            return _usage_defaults(
                f"usage has no key {k!r}; it takes " + ", ".join(USAGE_DEFAULTS)
            )
        if not isinstance(v, dict):
            return _usage_defaults(f"usage.{k} must be a mapping")
        for name, value in v.items():
            if k == "subscriptions":
                fee = dict(value).get("fee", 0) if isinstance(value, dict) else None
                if (
                    not isinstance(value, dict)
                    or isinstance(fee, bool)
                    or not (isinstance(fee, (int, float)) and fee >= 0)
                ):
                    return _usage_defaults(
                        f"usage.subscriptions.{name} must be a mapping with a"
                        " non-negative fee"
                    )
            elif not isinstance(value, str) or not value:
                return _usage_defaults(f"usage.{k}.{name} {value!r} is not a name")
        out[k] = {str(n): val for n, val in v.items()}
    return out


# When a notice turns on, escalates and clears (notices.py). The gap between
# on and clear keeps a reading that hovers at the threshold from flapping.
NOTICES_DEFAULTS = {
    "disk": {"on": 90, "critical": 97, "clear": 85},
    "memory": {"on": 90, "critical": 95, "clear": 85, "psi_full": 5},
    "cpu": {"on": 50, "clear": 30},
    "quota": True,
}


def _notices_defaults() -> dict:
    """The host kinds' defaults, then every declared per-session source's
    (notice_sources.py)."""
    return {
        k: dict(v) if isinstance(v, dict) else v for k, v in NOTICES_DEFAULTS.items()
    } | notice_sources.declared()


def _source_from(name: str, v: object, default: dict) -> dict | bool | str:
    """A declared source's entry over its defaults: false turns it off, true
    keeps the defaults. A string is the error."""
    if v is False:
        return False
    if v is True:
        return dict(default)
    if not isinstance(v, dict):
        return f"notices.{name} must be true, false or a mapping"
    for k, n in v.items():
        if k not in default:
            keys = ", ".join(default) or "no keys"
            return f"notices.{name} has no key {k!r}; it takes {keys}"
        d = default[k]
        if isinstance(d, bool):
            if not isinstance(n, bool):
                return f"notices.{name}.{k} {n!r} is not true or false"
        elif isinstance(d, int) and (
            isinstance(n, bool) or not isinstance(n, int) or n <= 0
        ):
            return f"notices.{name}.{k} {n!r} is not a whole number above 0"
        elif isinstance(n, bool) or not isinstance(n, int | float) or n <= 0:
            return f"notices.{name}.{k} {n!r} is not a number above 0"
    return default | v


def notices_from(raw: object) -> dict:
    """The ``notices:`` block over its defaults. ``false`` in place of a kind
    or a source turns it off. A wrong block keeps every default in force and
    carries ``error`` for aegis doctor, so a typo never stops a send. A name
    no installed source declares is kept as given and listed in
    ``undeclared``, a warning: removing a plugin must not break the file."""
    out = _notices_defaults()
    if raw is None:
        return out
    sources = notice_sources.declared()
    kinds = ", ".join([*NOTICES_DEFAULTS, *sources])
    if not isinstance(raw, dict):
        return out | {"error": f"notices must be a mapping of {kinds}"}
    for kind, v in raw.items():
        if kind in sources:
            got = _source_from(str(kind), v, sources[str(kind)])
            if isinstance(got, str):
                return _notices_defaults() | {"error": got}
            out[kind] = got
            continue
        if kind not in NOTICES_DEFAULTS:
            out[kind] = v
            out.setdefault("undeclared", []).append(str(kind))
            continue
        default = NOTICES_DEFAULTS[str(kind)]
        if v is False or (kind == "quota" and v is True):
            out[kind] = v
            continue
        if not isinstance(default, dict) or not isinstance(v, dict):
            what = "true or false" if kind == "quota" else "a mapping or false"
            return _notices_defaults() | {"error": f"notices.{kind} must be {what}"}
        for k, n in v.items():
            if k not in default:
                keys = ", ".join(default)
                return _notices_defaults() | {
                    "error": f"notices.{kind} has no key {k!r}; it takes {keys}"
                }
            if (
                isinstance(n, bool)
                or not isinstance(n, int | float)
                or not 0 <= n <= 100
            ):
                return _notices_defaults() | {
                    "error": f"notices.{kind}.{k} {n!r} is not a number from 0 to 100"
                }
        levels: dict = default | v
        # An ``on`` alone moves clear and critical with it, by the defaults'
        # gaps, so {on: 80} does not sit below the default clear.
        if "on" in v and "clear" not in v:
            levels["clear"] = max(levels["on"] - (default["on"] - default["clear"]), 0)
        if "on" in v and "critical" in default and "critical" not in v:
            levels["critical"] = min(
                levels["on"] + (default["critical"] - default["on"]), 100
            )
        if not levels["clear"] < levels["on"] <= levels.get("critical", 100):
            order = "clear < on <= critical" if "critical" in levels else "clear < on"
            return _notices_defaults() | {
                "error": f"notices.{kind} must keep {order}, not "
                + ", ".join(
                    f"{k} {levels[k]}"
                    for k in ("clear", "on", "critical")
                    if k in levels
                )
            }
        out[kind] = levels
    return out


# How many workflow node sessions may be open at once on this server, every
# run together (workflows.py): ``total`` for all agents, and a number per
# agent alias for that alias alone. Both out-of-memory kills the workflows
# design measured came from too many sessions at once.
WORKFLOWS_DEFAULTS = {"max_agents": {"total": 4}}


def _positive(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 1


def workflows_from(raw: object) -> dict:
    """The ``workflows:`` block over its defaults. ``max_agents`` is
    ``{total: N, <agent>: M, ...}``, or a bare N for ``{total: N}``; a
    mapping without ``total`` keeps the default total. A wrong block keeps
    the default in force and carries ``error`` for aegis doctor, as the
    context block does. Whether each alias names an agent is the doctor's
    to say, since the agents are another block."""
    default: dict = {"max_agents": dict(WORKFLOWS_DEFAULTS["max_agents"])}
    if raw is None:
        return default
    if not isinstance(raw, dict):
        return default | {"error": "workflows must be a mapping with max_agents"}
    m: object = default["max_agents"]
    for k, v in raw.items():
        if k != "max_agents":
            return default | {
                "error": f"workflows has no key {k!r}; it takes max_agents"
            }
        m = v
    if _positive(m):
        return {"max_agents": {"total": m}}
    if not isinstance(m, dict):
        return default | {
            "error": f"workflows.max_agents {m!r} is not {{total: N, <agent>: M}}"
        }
    for k, v in m.items():
        if not _positive(v):
            return default | {
                "error": f"workflows.max_agents.{k} {v!r} is not a positive integer"
            }
    return {"max_agents": default["max_agents"] | {str(k): v for k, v in m.items()}}


# When aegis nudges an agent (nudges.py): ``silent_s`` after a turn that
# ended without turn_end (false: never), and ``plan_min`` minutes of work on a
# plan left untouched, at the end of a turn.
NUDGES_DEFAULTS: dict = {"silent_s": 2, "plan_min": 15}


KNOWLEDGE_DEFAULTS: dict = {"embeddings": "off", "know_how": True}


def knowledge_from(raw: object) -> dict:
    """The ``knowledge:`` block over its defaults: ``embeddings``, off or a
    fastembed model's name (knowledge/embed.py), and ``know_how``, whether a
    session is told about a repository's docs (knowledge/know_how.py). A wrong
    block keeps the defaults and carries ``error`` for aegis doctor."""
    out = dict(KNOWLEDGE_DEFAULTS)
    if raw is None:
        return out
    if not isinstance(raw, dict):
        return out | {
            "error": "knowledge must be a mapping with embeddings and know_how"
        }
    for k, v in raw.items():
        if k not in KNOWLEDGE_DEFAULTS:
            return out | {
                "error": f"knowledge has no key {k!r}; it takes embeddings and know_how"
            }
        if k == "know_how":
            if not isinstance(v, bool):
                return out | {"error": f"knowledge.know_how {v!r} is not true or false"}
            out["know_how"] = v
            continue
        if v is False:
            v = "off"
        if not isinstance(v, str) or not v.strip():
            return out | {
                "error": f"knowledge.embeddings {v!r} is not off or a model's name"
            }
        out["embeddings"] = v.strip()
    return out


def nudges_from(raw: object) -> dict:
    """The ``nudges:`` block over its defaults. A wrong block keeps the
    defaults and carries ``error`` for aegis doctor."""
    out = dict(NUDGES_DEFAULTS)
    if raw is None:
        return out
    if not isinstance(raw, dict):
        return out | {"error": "nudges must be a mapping of silent_s and plan_min"}
    for k, v in raw.items():
        if k not in NUDGES_DEFAULTS:
            return dict(NUDGES_DEFAULTS) | {
                "error": f"nudges has no key {k!r}; it takes silent_s, plan_min"
            }
        if k == "silent_s" and v is False:
            out[k] = False
            continue
        low = 0 if k == "silent_s" else 0.0
        if (
            isinstance(v, bool)
            or not isinstance(v, int | float)
            or v < low
            or (k == "plan_min" and v == 0)
        ):
            what = (
                "a number of seconds from 0, or false"
                if k == "silent_s"
                else ("a number of minutes above 0")
            )
            return dict(NUDGES_DEFAULTS) | {"error": f"nudges.{k} {v!r} is not {what}"}
        out[k] = v
    return out


def park_from(raw: object) -> dict:
    """The ``park:`` block: each harness named in it, and the seconds one of its
    sessions must be safe to end before aegis ends its process (park.py). A
    harness it does not name never parks. A wrong block parks nothing and
    carries ``error`` for aegis doctor."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        return {"error": "park must be a mapping of harness to seconds"}
    out: dict = {}
    for k, v in raw.items():
        if k not in HARNESSES:
            return {
                "error": f"park has no harness {k!r}; it takes {', '.join(HARNESSES)}"
            }
        if isinstance(v, bool) or not isinstance(v, int) or v < 1:
            return {"error": f"park.{k} {v!r} is not a positive number of seconds"}
        out[k] = v
    return out


def park_after(park: dict, harness: str) -> int | None:
    """The seconds before a session of ``harness`` parks; None when it never does."""
    return None if "error" in park else park.get(harness)


# What a launch on a harness version outside the tested range does
# (harness_versions.py): who started it picks the policy. ``accepted`` lists,
# per harness, versions a person allowed for good.
HV_DEFAULTS: dict = {
    "person": "ask",
    "agent": "warn",
    "startup": "warn",
    "accepted": {},
}
HV_VALUES = {
    "person": ("ask", "warn", "off"),
    "agent": ("warn", "off"),
    "startup": ("warn", "off"),
}


def _hv_defaults() -> dict:
    return HV_DEFAULTS | {"accepted": {}}


def harness_versions_from(raw: object) -> dict:
    """The ``harness_versions:`` block over its defaults. A wrong block keeps
    every default in force and carries ``error``, as the context block does:
    a typo must not start asking, or stop asking, at every launch."""
    out = _hv_defaults()
    if raw is None:
        return out
    if not isinstance(raw, dict):
        return out | {
            "error": "harness_versions must be a mapping of person, agent, startup, accepted"
        }
    for k, v in raw.items():
        if k == "accepted":
            if v is None:
                continue
            if not isinstance(v, dict):
                return _hv_defaults() | {
                    "error": "harness_versions.accepted must map a harness to a list of versions"
                }
            for h, versions in v.items():
                if h not in HARNESSES:
                    return _hv_defaults() | {
                        "error": f"harness_versions.accepted has no harness {h!r};"
                        f" it takes {', '.join(HARNESSES)}"
                    }
                if not isinstance(versions, list):
                    return _hv_defaults() | {
                        "error": f"harness_versions.accepted.{h} is not a list of versions"
                    }
                out["accepted"][h] = [str(x) for x in versions]
        elif isinstance(k, str) and k in HV_VALUES:
            if k == "agent" and v == "ask":
                return _hv_defaults() | {
                    "error": "harness_versions.agent cannot be ask: nobody is there"
                    " to answer when an agent starts a session; it takes warn, off"
                }
            if v not in HV_VALUES[k]:
                return _hv_defaults() | {
                    "error": f"harness_versions.{k} {v!r} is not one of"
                    f" {', '.join(HV_VALUES[k])}"
                }
            out[k] = v
        else:
            return _hv_defaults() | {
                "error": f"harness_versions has no key {k!r};"
                " it takes person, agent, startup, accepted"
            }
    return out


# The sections of the Settings page drawn from the wire's schema alone: each
# block that is one value, or a mapping of values over its defaults. Agents,
# queues, terminals, parking, workflows and untested versions have controls
# of their own in settings.js.
SECTIONS = {
    "generators": (
        "Recaps and side notes",
        "A configured agent writes a recap when a person returns to a long unread"
        " stretch, and answers /btw side questions. Blank turns each off.",
    ),
    "memory": (
        "Memory",
        "The memory one session, or one terminal, may use: a size (3G, 512M), a"
        " share of the host's RAM (40%) or max. Blank is 40%.",
    ),
    "context": (
        "Context",
        "From handoff_at tokens turn_end tells an agent its context size (0 is"
        " off). A tab warns that its cache went cold after cold_after seconds"
        " idle with at least cold_min tokens.",
    ),
    "nudges": (
        "Nudges",
        "Seconds after a turn that ended without turn_end, and minutes of work on"
        " a plan left untouched, before aegis asks the agent where it stands.",
    ),
    "notices": (
        "Notices",
        "When aegis tells agents that the host runs short, and what each source"
        " reports. Off turns a kind off; blank keeps its default.",
    ),
    "network": (
        "Network",
        "What the Net, Exit and Speed readings reach, and how often, in seconds."
        " A speed_interval of 0 turns the speed test off.",
    ),
    "embeddings": (
        "Search embeddings",
        "Embeddings: off, or a fastembed model fused with keyword search, such"
        " as minishlab/potion-multilingual-128M. It needs the embeddings extra"
        " and about a gigabyte of memory, and is read when aegis starts.",
    ),
    "host": (
        "Host",
        "The disks the Host meters list: every block device the host mounts. Mount"
        " points to hide, and ones to add that are not block devices (ZFS, NFS).",
    ),
}


def fields() -> list[dict]:
    """The keys the Settings page draws from the wire, in order. ``kind`` says
    which control: agent (a configured generation agent), size, int, number, bool, text, list
    (of strings) or switch (a notice kind, written ``false`` when off);
    ``off`` marks a number the file may set to false."""
    out: list[dict] = []

    def add(section: str, key: str, kind: str, default: object, **more) -> None:
        out.append({"section": section, "key": key, "kind": kind} | more | {
            "default": default
        })  # fmt: skip

    add("generators", "recap.agent", "agent", None)
    add("generators", "btw.agent", "agent", None)
    add("memory", "session_memory_max", "size", DEFAULT_MEMORY_MAX)
    add("memory", "terminal_memory_max", "size", DEFAULT_MEMORY_MAX)
    for k, v in CONTEXT_DEFAULTS.items():
        add("context", f"context.{k}", "int", v)
    add("nudges", "nudges.silent_s", "number", NUDGES_DEFAULTS["silent_s"], off=True)
    add("nudges", "nudges.plan_min", "number", NUDGES_DEFAULTS["plan_min"])
    for kind, d in _notices_defaults().items():
        add("notices", f"notices.{kind}", "switch", True)
        for k, v in (d if isinstance(d, dict) else {}).items():
            what = (
                "bool"
                if isinstance(v, bool)
                else "int"
                if isinstance(v, int)
                else "number"
            )
            add("notices", f"notices.{kind}.{k}", what, v)
    net = NetConfig()
    for k in NET_KEYS:
        v = getattr(net, k)
        if k == "anchors":
            add("network", "network.anchors", "list", [_anchor(*a) for a in v])
        else:
            add("network", f"network.{k}", "bool" if k == "enabled" else "number", v)
    add("embeddings", "knowledge.embeddings", "text", KNOWLEDGE_DEFAULTS["embeddings"])
    add("embeddings", "knowledge.know_how", "bool", KNOWLEDGE_DEFAULTS["know_how"])
    for k, v in host_from(None)[0].items():
        add("host", f"host.{k}", "list", v)
    return out


def _anchor(host: str, port: int) -> str:
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def session_memory_max_from(
    raw: object, key: str = "session_memory_max"
) -> tuple[object, str | None]:
    """A memory cap for one child's cgroup (cgroups.py) and, for a value that
    is not a size, why; a wrong one keeps the default, as the context block
    does. Sessions and terminals each have one."""
    if raw is None:
        return DEFAULT_MEMORY_MAX, None
    try:
        parse_size(raw, 1)
    except ValueError as e:
        return DEFAULT_MEMORY_MAX, f"{key}: {e}"
    return raw, None


def stamp_of(path: Path) -> Stamp | None:
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    return (st.st_mtime_ns, st.st_size, st.st_ino)


def stamp_token(stamp: Stamp | None) -> str | None:
    """The stamp as the browser carries it. A string, because st_mtime_ns is
    about 1.8e18 and a JavaScript number holds integers exactly only up to
    2**53: a numeric stamp came back rounded and every save looked stale."""
    return ":".join(map(str, stamp)) if stamp else None


def stamp_from(token: str | None) -> Stamp | None:
    if token is None:
        return None
    try:
        a, b, c = (int(x) for x in token.split(":"))
    except ValueError as e:
        raise OpError("bad_params", f"stamp {token!r} is not one aegis sent") from e
    return (a, b, c)


def load(path: Path) -> tuple[dict | None, str | None]:
    """The file's top-level mapping, or why it has none."""
    try:
        text = path.read_text()
        data = YAML(typ="safe").load(text)
    except (YAMLError, OSError, UnicodeDecodeError) as e:
        return None, f"{path}: {e}"
    if not text.strip():
        return None, f"{path}: the file is empty"
    if not isinstance(data, dict):
        return None, f"{path}: the top level is not a mapping"
    return data, None


@dataclass(frozen=True)
class Finding:
    level: Literal["ok", "warn", "error"]
    where: str  # "file", "agents.deepseek.model", "harness.opencode", "state"
    message: str
    # The Settings row it marks: "agents.<name>", "queues.<name>",
    # "default_agent", or None for the file, a harness and the state.
    row: str | None = None

    def wire(self) -> dict:
        return asdict(self)


class AgentDoc(BaseModel):
    model_config = {"extra": "forbid"}
    name: str
    harness: str = ""
    model: str = ""
    # The cascade, when the agent names two or more models; empty for a
    # one-model agent, which uses ``model``.
    models: list[str] = Field(default_factory=list)
    effort: str = ""
    permission: str = ""
    priming: str | None = None
    keep_harness_tools: list[str] = Field(default_factory=list)
    # A linked server to exit through, or "off"; blank: the server's exit_via.
    exit: str = ""


class QueueDoc(BaseModel):
    model_config = {"extra": "forbid"}
    name: str
    agent: str = ""
    max_parallel: int | None = None
    # None: the file leaves it out, which reads as true.
    quiet: bool | None = None


class TerminalDoc(BaseModel):
    model_config = {"extra": "forbid"}
    name: str
    cmd: str = ""
    cwd: str = ""
    restart: str = ""  # blank: left out, which reads as no
    env: dict[str, str] = Field(default_factory=dict)
    access: dict[str, str] = Field(default_factory=dict)


class HarnessVersionsDoc(BaseModel):
    model_config = {"extra": "forbid"}
    person: str = HV_DEFAULTS["person"]
    agent: str = HV_DEFAULTS["agent"]
    startup: str = HV_DEFAULTS["startup"]
    accepted: dict[str, list[str]] = Field(default_factory=dict)


class UsageDoc(BaseModel):
    model_config = {"extra": "forbid"}
    owners: dict[str, str] = Field(default_factory=dict)
    owner_names: dict[str, str] = Field(default_factory=dict)
    subscriptions: dict[str, dict[str, str | int | float]] = Field(default_factory=dict)


class ConfigDoc(BaseModel):
    """What the Settings form edits and ``aegis init`` proposes."""

    model_config = {"extra": "forbid"}
    agents: list[AgentDoc] = Field(default_factory=list)
    default_agent: str | None = None
    exit_via: str | None = None
    queues: list[QueueDoc] = Field(default_factory=list)
    park: dict[str, int] = Field(default_factory=dict)
    # The workflows: block's max_agents: total and a bound per agent alias;
    # empty when the file has no block, or one the reader refuses.
    max_agents: dict[str, int] = Field(default_factory=dict)
    # The harness_versions: block; the defaults when the file has none, or
    # one the reader refuses.
    harness_versions: HarnessVersionsDoc = Field(default_factory=HarnessVersionsDoc)
    terminals: list[TerminalDoc] = Field(default_factory=list)
    # The usage: block; empty when the file has none, or one the reader refuses.
    usage: UsageDoc = Field(default_factory=UsageDoc)
    # Each key of fields() the file sets, by its dotted path; a switch only
    # when it is off (false).
    settings: dict[str, bool | int | float | str | list[str]] = Field(
        default_factory=dict
    )


def _exit_via(data: dict) -> str | None:
    v = data.get("exit_via")
    return str(v) if v else None


def doc_from(data: dict) -> ConfigDoc:
    agents = [
        AgentDoc(
            name=a.name,
            harness=a.harness,
            model=a.model,
            models=list(a.models) if len(a.models) > 1 else [],
            effort=a.effort,
            permission=a.permission,
            priming=a.priming,
            keep_harness_tools=_keep_raw(data, a.name),
            exit=a.exit,
        )
        for a in agents_from(data)
    ]
    queues = []
    raw = data.get("queues")
    for name, q in (raw if isinstance(raw, dict) else {}).items():
        q = q if isinstance(q, dict) else {}
        limit = q.get("max_parallel")
        ok = isinstance(limit, int) and not isinstance(limit, bool)
        queues.append(
            QueueDoc(
                name=str(name),
                agent=str(q.get("agent") or ""),
                max_parallel=limit if ok else None,
                quiet=q["quiet"] if isinstance(q.get("quiet"), bool) else None,
            )
        )
    d = data.get("default_agent")
    w = data.get("workflows")
    pool = workflows_from(w)
    hv = harness_versions_from(data.get("harness_versions"))
    usage = usage_from(data.get("usage"))
    return ConfigDoc(
        agents=agents,
        default_agent=str(d) if d else None,
        exit_via=_exit_via(data),
        queues=queues,
        park={k: v for k, v in park_from(data.get("park")).items() if k != "error"},
        max_agents={} if w is None or "error" in pool else pool["max_agents"],
        harness_versions=HarnessVersionsDoc(
            **{k: v for k, v in hv.items() if k != "error"}
        ),
        terminals=_terminals_doc(data.get("terminals")),
        usage=UsageDoc() if "error" in usage else UsageDoc(**usage),
        settings=_settings_doc(data),
    )


def _keep_raw(data: dict, name: str) -> list[str]:
    """The agent's keep_harness_tools as the file has it, so a save does not
    drop a list the reader refuses: it stays on the card, with its error."""
    node = (data.get("agents") or {}).get(name)
    node = node if isinstance(node, dict) else {}
    provider = node.get("provider")
    raw = node.get("keep_harness_tools")
    if isinstance(provider, dict) and "keep_harness_tools" in provider:
        raw = provider["keep_harness_tools"]
    return [str(t) for t in raw] if isinstance(raw, list) else []


def _terminals_doc(raw: object) -> list[TerminalDoc]:
    out = []
    entries: dict = raw if isinstance(raw, dict) else {}
    for name, entry in entries.items():
        t = {str(k): v for k, v in entry.items()} if isinstance(entry, dict) else {}

        def strs(k: str) -> dict[str, str]:
            m = t.get(k)
            return {str(a): str(b) for a, b in m.items()} if isinstance(m, dict) else {}

        out.append(
            TerminalDoc(
                name=str(name),
                cmd=str(t.get("cmd") or ""),
                cwd=str(t.get("cwd") or ""),
                restart=str(t.get("restart") or ""),
                env=strs("env"),
                access=strs("access"),
            )
        )
    return out


_MISSING = object()


def _at(data: object, key: str) -> object:
    """The value at a dotted path, or _MISSING when a step is not a mapping."""
    node = data
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = dict(node)[part]
    return node


def _settings_doc(data: dict) -> dict:
    out: dict = {}
    for f in fields():
        v = _at(data, f["key"])
        if f["kind"] == "switch":
            if v is False:
                out[f["key"]] = False
        elif v is not _MISSING and v is not None and not isinstance(v, dict):
            out[f["key"]] = [str(x) for x in v] if isinstance(v, list) else v
    return out


def _settings_blocks(settings: dict) -> dict:
    """The settings as the file would hold them: nested, without what sits
    under a switch that is off."""
    out: dict = {}
    for f in fields():
        key = f["key"]
        if key not in settings or _under_off(settings, key):
            continue
        *parents, last = key.split(".")
        node = out
        for part in parents:
            node = node.setdefault(part, {})
        node[last] = settings[key]
    return out


def _under_off(settings: dict, key: str) -> bool:
    parts = key.split(".")
    return any(settings.get(".".join(parts[:i])) is False for i in range(1, len(parts)))


@dataclass(frozen=True)
class Snapshot:
    path: Path
    exists: bool = False
    stamp: Stamp | None = None
    agents: tuple[Agent, ...] = ()
    default_agent: str | None = None
    exit_via: str | None = None
    queues: dict[str, dict] = field(default_factory=dict)
    unknown_keys: tuple[str, ...] = ()
    doc: ConfigDoc = field(default_factory=ConfigDoc)
    context: dict = field(default_factory=lambda: dict(CONTEXT_DEFAULTS))
    usage: dict = field(default_factory=_usage_defaults)
    notices: dict = field(default_factory=_notices_defaults)
    nudges: dict = field(default_factory=lambda: dict(NUDGES_DEFAULTS))
    knowledge: dict = field(default_factory=lambda: dict(KNOWLEDGE_DEFAULTS))
    workflows: dict = field(default_factory=lambda: dict(WORKFLOWS_DEFAULTS))
    park: dict = field(default_factory=dict)
    harness_versions: dict = field(default_factory=_hv_defaults)
    session_memory_max: object = DEFAULT_MEMORY_MAX
    session_memory_error: str | None = None
    terminals: dict[str, dict] = field(default_factory=dict)
    terminals_error: str | None = None
    terminal_memory_max: object = DEFAULT_MEMORY_MAX
    terminal_memory_error: str | None = None
    network: NetConfig = field(default_factory=NetConfig)
    network_error: str | None = None
    host: dict = field(default_factory=lambda: host_from(None)[0])
    host_error: str | None = None
    # The multi-user mode (users.py); read once, at boot (cli.serve).
    multi_user: MultiUser | None = None
    multi_user_error: str | None = None
    # Why the file on disk is not the one in force, when it does not parse.
    error: str | None = None

    @classmethod
    def parse(cls, path: Path, stamp: Stamp | None, data: dict) -> Snapshot:
        d = data.get("default_agent")
        mem, mem_error = session_memory_max_from(data.get("session_memory_max"))
        terms, terms_error = terminals_from(data.get("terminals"))
        tmem, tmem_error = session_memory_max_from(
            data.get("terminal_memory_max"), "terminal_memory_max"
        )
        network, network_error = network_from(data.get("network"))
        host, host_error = host_from(data.get("host"))
        multi_user, multi_user_error = multi_user_from(data)
        return cls(
            path=path,
            exists=True,
            stamp=stamp,
            agents=tuple(agents_from(data)),
            default_agent=str(d) if d else None,
            exit_via=_exit_via(data),
            queues=queues_from(data.get("queues")),
            unknown_keys=tuple(str(k) for k in data if k not in TOP_KEYS),
            doc=doc_from(data),
            context=context_from(data.get("context")),
            usage=usage_from(data.get("usage")),
            notices=notices_from(data.get("notices")),
            nudges=nudges_from(data.get("nudges")),
            knowledge=knowledge_from(data.get("knowledge")),
            workflows=workflows_from(data.get("workflows")),
            park=park_from(data.get("park")),
            harness_versions=harness_versions_from(data.get("harness_versions")),
            session_memory_max=mem,
            session_memory_error=mem_error,
            terminals=terms,
            terminals_error=terms_error,
            terminal_memory_max=tmem,
            terminal_memory_error=tmem_error,
            network=network,
            network_error=network_error,
            host=host,
            host_error=host_error,
            multi_user=multi_user,
            multi_user_error=multi_user_error,
        )

    def wire(self) -> dict:
        return {
            "path": str(self.path),
            "root": str(self.path.parent),
            "exists": self.exists,
            "stamp": stamp_token(self.stamp),
            "error": self.error,
            "unknown_keys": list(self.unknown_keys),
            "doc": self.doc.model_dump(),
            "vocab": {
                "harnesses": list(HARNESSES),
                "efforts": list(EFFORTS),
                "permissions": list(PERMISSION_ORDER),
                "tools": list(REPLACED_TOOLS),
                "restarts": list(RESTARTS),
            },
            "schema": fields(),
            "sections": {k: {"title": t, "note": n} for k, (t, n) in SECTIONS.items()},
        }


class Config:
    def __init__(
        self,
        config_root: Path,
        on_change: Callable[[Snapshot], None] = lambda s: None,
    ) -> None:
        self.path = config_root / CONFIG_FILE
        self._on_change = on_change
        self._snap = self._read(stamp_of(self.path), Snapshot(self.path))

    def _read(self, stamp: Stamp | None, last: Snapshot) -> Snapshot:
        if stamp is None:
            return Snapshot(self.path)
        data, error = load(self.path)
        if data is None:
            return replace(last, exists=True, stamp=stamp, error=error)
        return Snapshot.parse(self.path, stamp, data)

    def current(self) -> Snapshot:
        """The config in force: one ``stat``, and a re-read when it moved."""
        stamp = stamp_of(self.path)
        if stamp != self._snap.stamp:
            self._snap = self._read(stamp, self._snap)
            self._on_change(self._snap)
        return self._snap

    async def watch(self, every: float = WATCH_EVERY_S) -> None:
        while True:
            await asyncio.sleep(every)
            try:
                self.current()
            except Exception:
                log.exception("reading %s", self.path)


def validate(doc: ConfigDoc) -> list[Finding]:
    """What stops ``doc`` being written: each a Finding on its Settings row."""
    out: list[Finding] = []

    def err(row: str, where: str, message: str) -> None:
        out.append(Finding("error", where, message, row))

    _validate_terminals(doc, err)
    _validate_settings(doc, err)

    names = [a.name for a in doc.agents]
    for a in doc.agents:
        row = f"agents.{a.name}"
        if not a.name.strip():
            err(row, row, "an agent needs a name")
        elif names.count(a.name) > 1:
            err(row, row, f"two agents are named {a.name!r}")
        if a.harness and a.harness not in HARNESSES:
            err(
                row,
                f"{row}.harness",
                f"harness {a.harness!r} is not one of {', '.join(HARNESSES)}",
            )
            continue
        raw = a.model_dump(exclude={"name", "models" if not a.models else "model"})
        (agent,) = agents_from({"agents": {a.name: raw}})
        if agent.error:
            err(row, row, agent.error)
    queues = [q.name for q in doc.queues]
    for q in doc.queues:
        row = f"queues.{q.name}"
        if not q.name.strip():
            err(row, row, "a queue needs a name")
        elif queues.count(q.name) > 1:
            err(row, row, f"two queues are named {q.name!r}")
        if q.agent not in names:
            err(
                row,
                f"{row}.agent",
                f"no agent named {q.agent!r}" if q.agent else "agent is missing",
            )
        if q.max_parallel is None or q.max_parallel < 1:
            err(row, f"{row}.max_parallel", "max_parallel is not a positive integer")
    if doc.default_agent and doc.default_agent not in names:
        err("default_agent", "default_agent", f"no agent named {doc.default_agent!r}")
    if "error" in (p := park_from(doc.park or None)):
        out.append(Finding("error", "park", p["error"]))
    hv = harness_versions_from(doc.harness_versions.model_dump())
    if "error" in hv:
        out.append(
            Finding("error", "harness_versions", hv["error"], "harness_versions")
        )
    if "error" in (u := usage_from(doc.usage.model_dump())):
        out.append(Finding("error", "usage", u["error"], "usage"))
    if doc.max_agents:
        # The rules aegis doctor applies to the block, each on its row.
        pool = workflows_from({"max_agents": doc.max_agents})
        if "error" in pool:
            err("workflows", "workflows", pool["error"])
        else:
            m = pool["max_agents"]
            for k, n in m.items():
                if k == "total":
                    continue
                where = f"workflows.max_agents.{k}"
                if k not in names:
                    err(f"workflows.{k}", where, f"no agent named {k!r}")
                elif n > m["total"]:
                    err(f"workflows.{k}", where, f"{k} {n} is above total {m['total']}")
    return out


def _validate_terminals(doc: ConfigDoc, err: Callable[[str, str, str], None]) -> None:
    names = [t.name for t in doc.terminals]
    for t in doc.terminals:
        row = f"terminals.{t.name}"
        if not t.name.strip():
            err(row, row, "a terminal profile needs a name")
        elif names.count(t.name) > 1:
            err(row, row, f"two terminal profiles are named {t.name!r}")
        elif error := terminals_from({t.name: _profile_raw(t)})[0][t.name].get("error"):
            err(row, row, error)


def _profile_raw(t: TerminalDoc) -> dict:
    raw: dict = {"cmd": t.cmd, "cwd": t.cwd}
    if t.restart:
        raw["restart"] = t.restart
    return raw | {k: v for k, v in (("env", t.env), ("access", t.access)) if v}


def _validate_settings(doc: ConfigDoc, err: Callable[[str, str, str], None]) -> None:
    """The rules each block's reader applies, on the block's section."""
    known = {f["key"] for f in fields()}
    for k in doc.settings:
        if k not in known:
            err(k.split(".")[0], k, f"aegis does not read {k!r}")
    b = _settings_blocks(doc.settings)
    for key in ("session_memory_max", "terminal_memory_max"):
        if key in b and (e := session_memory_max_from(b[key], key)[1]):
            err(key, key, e)
    readers: dict[str, Callable[[object], object]] = {
        "context": lambda v: context_from(v).get("error"),
        "nudges": lambda v: nudges_from(v).get("error"),
        "notices": lambda v: notices_from(v).get("error"),
        "network": lambda v: network_from(v)[1],
        "host": lambda v: host_from(v)[1],
        "knowledge": lambda v: knowledge_from(v).get("error"),
    }
    for block, error_of in readers.items():
        if block in b and (e := error_of(b[block])):
            err(block, block, str(e))
    agents = {a.name: a for a in doc.agents}
    for key in ("recap", "btw"):
        name = doc.settings.get(f"{key}.agent")
        if not name:
            continue
        a = agents.get(str(name))
        if a is None:
            err(key, f"{key}.agent", f"no agent named {name!r}")


def _put(node: dict, key: str, value: object) -> None:
    # Only a changed value is assigned, so an untouched one keeps its quotes.
    if node.get(key) != value:
        node[key] = value


def _section(data: CommentedMap, key: str) -> CommentedMap:
    node = data.get(key)
    if not isinstance(node, CommentedMap):
        node = data[key] = CommentedMap()
    return node


def _entries(data: CommentedMap, key: str, names: list[str]) -> CommentedMap:
    """The ``key`` map holding exactly ``names``: gone ones deleted, new ones
    appended at the end."""
    section = _section(data, key)
    for name in [k for k in section if k not in names]:
        del section[name]
    for name in names:
        if not isinstance(section.get(name), dict):
            section[name] = CommentedMap()
    return section


def _set_agent(node: CommentedMap, a: AgentDoc) -> None:
    provider = node.get("provider")
    if isinstance(provider, dict):
        _put(provider, "name", a.harness)
    elif isinstance(provider, str):
        _put(node, "provider", a.harness)
    else:
        _put(node, "harness", a.harness)
    for f in ("effort", "permission"):
        holder = provider if isinstance(provider, dict) and f in provider else node
        _put(holder, f, getattr(a, f))
    # Two or more models are written as models:, one as model:, unless the
    # file already lists it under models:, which a person wrote as a list.
    held = isinstance(provider, dict) and ("model" in provider or "models" in provider)
    holder = provider if held else node
    if len(a.models) > 1:
        holder.pop("model", None)
        _put(holder, "models", list(a.models))
    elif isinstance(listed := holder.get("models"), list):
        holder.pop("model", None)
        if list(listed) != [a.model]:
            listed[:] = [a.model]  # in place, so the list keeps its style
    else:
        holder.pop("models", None)
        _put(holder, "model", a.model)
    if a.priming:
        if node.get("priming") != a.priming:
            node["priming"] = (
                LiteralScalarString(a.priming) if "\n" in a.priming else a.priming
            )
    else:
        node.pop("priming", None)
    keeper = (
        provider
        if isinstance(provider, dict) and "keep_harness_tools" in provider
        else node
    )
    if a.keep_harness_tools:
        _put(keeper, "keep_harness_tools", list(a.keep_harness_tools))
    else:
        keeper.pop("keep_harness_tools", None)
    if a.exit:
        _put(node, "exit", a.exit)
    else:
        node.pop("exit", None)


def _apply(data: CommentedMap, doc: ConfigDoc) -> None:
    if doc.default_agent:
        _put(data, "default_agent", doc.default_agent)
    else:
        data.pop("default_agent", None)
    if doc.exit_via:
        _put(data, "exit_via", doc.exit_via)
    else:
        data.pop("exit_via", None)
    if doc.agents:
        agents = _entries(data, "agents", [a.name for a in doc.agents])
        for a in doc.agents:
            _set_agent(agents[a.name], a)
    else:
        data.pop("agents", None)
    if doc.queues:
        queues = _entries(data, "queues", [q.name for q in doc.queues])
        for q in doc.queues:
            _put(queues[q.name], "agent", q.agent)
            _put(queues[q.name], "max_parallel", q.max_parallel)
            if q.quiet is None:
                queues[q.name].pop("quiet", None)
            else:
                _put(queues[q.name], "quiet", q.quiet)
    else:
        data.pop("queues", None)
    if doc.park:
        park = _section(data, "park")
        for h in list(park):
            if h not in doc.park:
                del park[h]
        for h, s in doc.park.items():
            _put(park, h, s)
    else:
        data.pop("park", None)
    block = data.get("workflows")
    if doc.max_agents:
        want = workflows_from({"max_agents": doc.max_agents})["max_agents"]
        # An untouched block compares equal and keeps its style and comments.
        if workflows_from(block).get("max_agents") != want:
            _section(data, "workflows")["max_agents"] = dict(doc.max_agents)
    elif block is not None and "error" not in workflows_from(block):
        del data["workflows"]  # every bound back to its default
    _apply_harness_versions(data, doc.harness_versions)
    _apply_usage(data, doc.usage)
    _apply_terminals(data, doc.terminals)
    _apply_settings(data, doc.settings)


def _apply_terminals(data: CommentedMap, terminals: list[TerminalDoc]) -> None:
    if not terminals:
        data.pop("terminals", None)
        return
    section = _entries(data, "terminals", [t.name for t in terminals])
    for t in terminals:
        node = section[t.name]
        _put(node, "cmd", t.cmd)
        _put(node, "cwd", t.cwd)
        if t.restart:
            _put(node, "restart", t.restart)
        else:
            node.pop("restart", None)
        for k in ("env", "access"):
            want = getattr(t, k)
            if not want:
                node.pop(k, None)
            elif dict(node.get(k) or {}) != want:
                node[k] = dict(want)


def _apply_settings(data: CommentedMap, settings: dict) -> None:
    """Each key of fields() set, or deleted when the form leaves it blank;
    keys the form does not know are kept."""
    for f in fields():
        key = f["key"]
        if _under_off(settings, key):
            continue
        v = settings.get(key)
        if f["kind"] == "switch":
            if v is False:
                _set_at(data, key, False)
            elif _at(data, key) is False:
                _del_at(data, key)
        elif v is None:
            _del_at(data, key)
        else:
            _set_at(data, key, v)


def _set_at(data: CommentedMap, key: str, value: object) -> None:
    *parents, last = key.split(".")
    node = data
    for part in parents:
        if not isinstance(node.get(part), dict):
            node[part] = CommentedMap()
        node = node[part]
    _put(node, last, value)


def _del_at(data: CommentedMap, key: str) -> None:
    """Delete the key, then each mapping above it that it left empty."""
    parts = key.split(".")
    nodes = [data]
    for part in parts[:-1]:
        nxt = nodes[-1].get(part)
        if not isinstance(nxt, dict):
            return
        nodes.append(nxt)
    nodes[-1].pop(parts[-1], None)
    for i in range(len(nodes) - 1, 0, -1):
        if nodes[i]:
            break
        del nodes[i - 1][parts[i - 1]]


def _apply_usage(data: CommentedMap, usage: UsageDoc) -> None:
    block = data.get("usage")
    want = usage.model_dump()
    if not any(want.values()):
        if block is not None and "error" not in usage_from(block):
            del data["usage"]
        return
    node = _section(data, "usage")
    for k, v in want.items():
        # An untouched map compares equal and keeps its style and comments.
        if not v:
            node.pop(k, None)
        elif usage_from({k: node.get(k)}).get(k) != v:
            node[k] = v


def _apply_harness_versions(data: CommentedMap, hv: HarnessVersionsDoc) -> None:
    block = data.get("harness_versions")
    have = harness_versions_from(block)
    want = hv.model_dump()
    want["accepted"] = {h: v for h, v in want["accepted"].items() if v}
    if "error" in have and want == _hv_defaults():
        return  # a block the reader refuses, untouched by this edit: keep it
    if {k: v for k, v in have.items() if k != "error"} == want:
        return  # untouched: keep its style and comments
    if want == _hv_defaults():
        data.pop("harness_versions", None)
        return
    node = _section(data, "harness_versions")
    for k in ("person", "agent", "startup"):
        if want[k] == HV_DEFAULTS[k]:
            node.pop(k, None)
        else:
            _put(node, k, want[k])
    if want["accepted"]:
        acc = _section(node, "accepted")
        for h in [h for h in acc if h not in want["accepted"]]:
            del acc[h]
        for h, versions in want["accepted"].items():
            if list(acc.get(h) or []) != versions:
                acc[h] = list(versions)
    else:
        node.pop("accepted", None)


def write(path: Path, doc: ConfigDoc, expected: Stamp | None) -> list[Finding]:
    """Write ``doc`` to ``path``. Returns the problems that stopped it; an
    empty list means it was written."""
    if stamp_of(path) != expected:
        raise OpError("stale", f"{path} changed on disk since it was read; reload it")
    if problems := validate(doc):
        return problems
    if expected is not None and (error := load(path)[1]):
        # The writer refuses what the reader refuses: a list at the top, a tag
        # the safe loader rejects, a comment-only file. Writing over one would
        # drop text the person has not seen the server read.
        raise OpError("bad_config", f"{error}; fix it in an editor first")
    rt = YAML()
    rt.preserve_quotes = True
    data = None
    if expected is not None:
        try:
            text = path.read_text()
            data = rt.load(text)
            # A file with a models: list keeps its own list indentation, such
            # as dashes indented by hand; any other file is written as before.
            _, seq, offset = load_yaml_guess_indent(text)
            if offset is not None and re.search(r"^\s*models:", text, re.M):
                rt.indent(mapping=2, sequence=seq, offset=offset)
        except YAMLError as e:
            raise OpError(
                "bad_config", f"{path} does not parse; fix it in an editor first: {e}"
            ) from e
    if not isinstance(data, CommentedMap):
        data = CommentedMap()
        data.yaml_set_start_comment("aegis configuration. `aegis doctor` checks it.")
    _apply(data, doc)
    buf = StringIO()
    rt.dump(data, buf)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(buf.getvalue())
    os.replace(tmp, path)
    return []


def write_text(path: Path, text: str, expected: Stamp | None) -> list[Finding]:
    """Write ``text`` to ``path`` as it is, comments and all: what a person's
    Apply on a config card writes (config_suggest.py). The same stamp check
    and validation as ``write``; an empty list means it was written."""
    if stamp_of(path) != expected:
        raise OpError("stale", f"{path} changed on disk since it was read; reload it")
    if problems := text_problems(text):
        return problems
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)
    return []


def text_problems(text: str) -> list[Finding]:
    """What stops ``text`` being written as .aegis.yaml: it does not parse,
    its top level is not a mapping, or ``validate`` finds an error."""
    try:
        data = YAML(typ="safe").load(text)
    except YAMLError as e:
        return [Finding("error", "file", f"does not parse: {e}")]
    if not isinstance(data, dict):
        return [Finding("error", "file", "the top level is not a mapping")]
    return validate(doc_from(data))


def accept_version(path: Path, harness: str, version: str) -> None:
    """Add ``version`` to ``harness_versions.accepted.<harness>``: what a
    person's "Always" does. Only that block is edited, so a finding elsewhere
    in the file (a broken queue) does not stop it. Raises OpError("stale")
    when the file moves under it, OpError("bad_config") when it cannot be
    written as it stands, and OpError("unwritable") when the disk refuses."""
    stamp = stamp_of(path)
    rt = YAML()
    rt.preserve_quotes = True
    data = None
    if stamp is not None:
        if error := load(path)[1]:
            raise OpError("bad_config", f"{error}; fix it in an editor first")
        try:
            data = rt.load(path.read_text())
        except YAMLError as e:
            raise OpError("bad_config", f"{path} does not parse: {e}") from e
    if not isinstance(data, CommentedMap):
        data = CommentedMap()
        data.yaml_set_start_comment("aegis configuration. `aegis doctor` checks it.")
    hv = harness_versions_from(data.get("harness_versions"))
    if "error" in hv:
        raise OpError("bad_config", hv["error"])
    if version in hv["accepted"].get(harness, []):
        return
    hv["accepted"].setdefault(harness, []).append(version)
    _apply_harness_versions(data, HarnessVersionsDoc(**hv))
    buf = StringIO()
    rt.dump(data, buf)
    if stamp_of(path) != stamp:
        raise OpError("stale", f"{path} changed on disk while it was edited; try again")
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text(buf.getvalue())
        os.replace(tmp, path)
    except OSError as e:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise OpError("unwritable", f"{path} cannot be written: {e}") from e
