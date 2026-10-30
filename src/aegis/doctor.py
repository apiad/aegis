"""What is installed here, and what is wrong with ``.aegis.yaml``. Reads only.

``detect`` looks for each harness aegis can run: its binary on PATH, its
``--version``, and its model catalog from the same ``probe`` the composer uses
(about 0.5 s for Claude and 3 s for OpenCode, no tokens). ``doctor`` checks the
file, the harnesses its agents name, every agent, the default, every queue and
the state directory, and returns findings a person can act on, each on the
Settings row it belongs to. A model the catalog does not list is a warning, not
an error: a CLI alias can resolve without being listed. ``propose`` is the
first config ``aegis init`` and the Settings page's Set up offer.

The doctor writes nothing: probe stderr goes to a temporary directory.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import shlex
import shutil
import tempfile
import time
from asyncio.subprocess import PIPE, STDOUT
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from . import launch, users
from .claude import login
from .agents import EFFORTS, HARNESSES, Agent
from .cgroups import parse_size, total_ram
from .claude.control import Catalog, Model
from .config import (
    AGENT_KEYS,
    QUEUE_KEYS,
    TOP_KEYS,
    AgentDoc,
    ConfigDoc,
    Finding,
    QueueDoc,
    Snapshot,
    load,
)
from .harness import harness_for
from .links import LinkStore
from .harness_versions import OUTSIDE, VERSION_TIMEOUT_S, judge
from .knowledge import embed
from .ranking import annotate, ranking
from .recap import load_recap
from .roots import CONFIG_FILE, Roots, legacy_state
from .ops import OpError
from .session import SpawnSpec

PROBE_TIMEOUT_S = 20.0
MARK = {"ok": "ok   ", "warn": "warn ", "error": "ERROR"}
LABELS = {
    "claude-code": "Claude Code",
    "opencode": "OpenCode",
    "codex": "Codex",
    "cursor": "Cursor Agent",
}


@dataclass(frozen=True)
class Found:
    harness: str
    bin: str | None  # absolute, or None when not on PATH
    version: str | None = None
    models: tuple[Model, ...] = ()
    error: str | None = None
    verdict: dict | None = None  # harness_versions.Verdict.wire()

    def wire(self) -> dict:
        return {
            "harness": self.harness,
            "label": LABELS[self.harness],
            "bin": self.bin,
            "version": self.version,
            "models": [m.wire() for m in self.models],
            "error": self.error,
            "verdict": self.verdict,
        }


async def _version(bin: str) -> str:
    proc = await launch.child(
        [bin, "--version"], stdout=PIPE, stderr=STDOUT, user=None, via=None
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), VERSION_TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"no answer in {VERSION_TIMEOUT_S:.0f} s") from None
    text = out.decode(errors="replace").strip()
    if proc.returncode:
        raise RuntimeError(f"exited {proc.returncode}: {text[:200]}")
    return text.splitlines()[0] if text else ""


async def _catalog(name: str, bins: dict[str, str], cwd: Path) -> Catalog:
    h = harness_for(
        name, bins["claude-code"], bins["opencode"], bins["codex"], bins["cursor"]
    )
    spec = SpawnSpec(
        agent="doctor", model="", effort="", permission="read", cwd=cwd, harness=name
    )
    with tempfile.TemporaryDirectory() as d:
        stderr = Path(d) / "stderr.log"
        try:
            return await asyncio.wait_for(h.probe(spec, stderr), PROBE_TIMEOUT_S)
        except Exception as e:
            tail = (
                stderr.read_text(errors="replace").strip().splitlines()[-3:]
                if stderr.exists()
                else []
            )
            raise RuntimeError("; ".join([str(e) or type(e).__name__, *tail])) from e


async def _find(
    name: str, bins: dict[str, str], cwd: Path, accepted: dict[str, list[str]]
) -> Found:
    bin = shutil.which(bins[name])
    if bin is None:
        return Found(name, None, error=f"{bins[name]} is not on PATH")
    failed = None
    try:
        version = await _version(bin)
    except (OSError, RuntimeError) as e:
        # Judged unknown, as a launch judges it: a warning, not an error (#454).
        version, failed = None, f"`{bin} --version` failed: {e}"
    verdict = judge(name, LABELS[name], version, os.path.realpath(bin), accepted).wire()
    if failed:
        verdict["message"] += f"\n{failed}"
    try:
        cat = await _catalog(name, {**bins, name: bin}, cwd)
    except RuntimeError as e:
        return Found(
            name,
            bin,
            version,
            error=f"{LABELS[name]} gave no model list: {e}",
            verdict=verdict,
        )
    return Found(name, bin, version, annotate(cat.models), verdict=verdict)


async def detect(
    cwd: Path,
    bins: dict[str, str],
    only: Iterable[str] = HARNESSES,
    accepted: dict[str, list[str]] | None = None,
) -> list[Found]:
    wanted = set(only)
    return list(
        await asyncio.gather(
            *(_find(h, bins, cwd, accepted or {}) for h in HARNESSES if h in wanted)
        )
    )


def propose(found: list[Found]) -> ConfigDoc:
    have = {f.harness: f for f in found if f.bin}
    agents: list[AgentDoc] = []
    if "claude-code" in have:
        agents.append(
            AgentDoc(
                name="opus",
                harness="claude-code",
                model="opus",
                effort="high",
                permission="full",
            )
        )
    catalog = have["opencode"].models if "opencode" in have else ()
    # Every model that costs nothing and can run an agent, best first, so a
    # first config spends no money and falls to the next free model when one
    # runs out. A paid model joins no cascade; with nothing free, the best
    # model alone is proposed, as before cascades, so an install with only
    # paid models still gets an agent.
    free = ranking().order([m.value for m in catalog if m.free])
    paid = [] if free else ranking().order([m.value for m in catalog])[:1]
    if free or paid:
        pick = free or paid
        first = next(m for m in catalog if m.value == pick[0])
        offered = [e for e in first.efforts if e in EFFORTS]
        effort = "high" if "high" in offered or not offered else offered[0]
        name = "free" if free else pick[0].rsplit("/", 1)[-1]
        if any(a.name == name for a in agents):
            name += "-opencode"
        agents.append(
            AgentDoc(
                name=name,
                harness="opencode",
                model=pick[0],
                models=free if len(free) > 1 else [],
                effort=effort,
                permission="full",
            )
        )
    if not agents:
        return ConfigDoc()
    default = agents[0].name
    queues = [QueueDoc(name="general", agent=default, max_parallel=3)]
    if free and default != "free":
        # Reviews and research on the free models, off the paid default.
        queues.append(QueueDoc(name="light", agent="free", max_parallel=3))
    return ConfigDoc(agents=agents, default_agent=default, queues=queues)


def _notices(cfg: dict) -> list[Finding]:
    """A wrong notices: block, and each name no installed source declares."""
    out = [Finding("error", "notices", cfg["error"])] if "error" in cfg else []
    out += [
        Finding(
            "warn",
            "notices",
            f"notices.{name}: no installed source declares it; it is ignored",
        )
        for name in cfg.get("undeclared", [])
    ]
    return out


def _unknown_keys(data: dict) -> list[Finding]:
    out = [
        Finding(
            "warn", str(k), f"aegis does not read {k!r}; it reads {', '.join(TOP_KEYS)}"
        )
        for k in data
        if k not in TOP_KEYS
    ]
    for section, known in (("agents", AGENT_KEYS), ("queues", QUEUE_KEYS)):
        entries = data.get(section)
        for name, raw in (entries if isinstance(entries, dict) else {}).items():
            if not isinstance(raw, dict):
                continue
            keys = list(raw)
            if section == "agents" and isinstance(raw.get("provider"), dict):
                keys += [k for k in raw["provider"] if k != "name"]
            row = f"{section}.{name}"
            out += [
                Finding(
                    "warn",
                    f"{row}.{k}",
                    f"aegis does not read {k!r}; it reads {', '.join(known)}",
                    row,
                )
                for k in keys
                if k not in known
            ]
    return out


def _agent(a: Agent, f: Found | None) -> list[Finding]:
    row = f"agents.{a.name}"
    if a.error:
        return [Finding("error", row, a.error, row)]
    if a.harness not in HARNESSES:
        return [
            Finding(
                "error",
                f"{row}.harness",
                f"aegis runs {', '.join(HARNESSES)}, not {a.harness!r}",
                row,
            )
        ]
    if f is not None and f.error:
        return [
            Finding(
                "error",
                f"{row}.harness",
                f"{LABELS[a.harness]} cannot run here: {f.error}",
                row,
            )
        ]
    out: list[Finding] = [
        Finding("warn", f"{row}.models", f"{m} is listed twice; the second is ignored", row)
        for m in a.repeated
    ]  # fmt: skip
    where = f"{row}.models" if len(a.models) > 1 else f"{row}.model"
    if (order := _out_of_order(a.models)) is not None:
        out.append(Finding("warn", where, order, row))
    catalog = f.models if f is not None else ()
    for model in a.models if catalog else ():
        m = next((m for m in catalog if model in (m.value, m.resolved)), None)
        if m is None:
            listed = ", ".join(x.value for x in catalog[:8])
            out.append(
                Finding(
                    "warn",
                    where,
                    f"{LABELS[a.harness]} does not list {model!r}; it lists {listed}",
                    row,
                )
            )
        elif m.efforts and a.effort not in m.efforts:
            out.append(
                Finding(
                    "warn",
                    f"{row}.effort",
                    f"{m.label} takes {', '.join(m.efforts)}, not {a.effort!r}",
                    row,
                )
            )
    return out or [
        Finding(
            "ok",
            row,
            f"{a.harness} {a.model}, effort {a.effort}, permission {a.permission}",
            row,
        )
    ]


def _intelligence(model: str) -> str:
    s = ranking().score(model)
    return (
        "unranked" if s is None or s.intelligence is None else f"{s.intelligence:.1f}"
    )


def _out_of_order(models: tuple[str, ...]) -> str | None:
    """The first model a cascade puts above one the ranking scores higher."""
    for above, better in zip(models, ranking().order(models)):
        if above != better:
            return f"{above} ({_intelligence(above)}) is above {better} ({_intelligence(better)}) in the ranking"
    return None


def _default(name: str | None, by_name: dict[str, Agent]) -> Finding:
    row = "default_agent"
    if not name:
        return Finding("error", row, "not set; a spawn that names no agent fails", row)
    a = by_name.get(name)
    if a is None:
        return Finding("error", row, f"no agent named {name!r}", row)
    if a.error:
        return Finding("error", row, f"agent {name!r} cannot spawn: {a.error}", row)
    return Finding("ok", row, name, row)


def _queue(name: str, q: dict, by_name: dict[str, Agent]) -> Finding:
    row = f"queues.{name}"
    if "error" in q:
        return Finding("error", row, q["error"], row)
    a = by_name.get(q["agent"])
    if a is None:
        return Finding(
            "error",
            f"{row}.agent",
            f"uses the agent {q['agent']!r}, which is not defined",
            row,
        )
    if a.error:
        return Finding(
            "error", f"{row}.agent", f"agent {a.name!r} cannot spawn: {a.error}", row
        )
    return Finding("ok", row, f"{a.name}, {q['max_parallel']} at a time", row)


def _recap(roots: Roots) -> list[Finding]:
    cfg = load_recap(roots.config_root)
    if cfg is None:
        return []
    if "error" in cfg:
        return [Finding("error", "recap", cfg["error"])]
    return [Finding("ok", "recap", f"{cfg['agent']}, {cfg['model']}")]


def _context(context: dict) -> Finding:
    if "error" in context:
        return Finding("error", "context", context["error"])
    return Finding(
        "ok",
        "context",
        f"handoff line from {context['handoff_at']:,} tokens, cold-cache notice "
        f"after {context['cold_after']} s idle over {context['cold_min']:,} tokens",
    )


def _park(park: dict) -> Finding:
    if "error" in park:
        return Finding("error", "park", park["error"])
    if not park:
        return Finding("ok", "park", "no harness parks its idle sessions")
    text = ", ".join(
        f"{h} after {s // 60} min" if s >= 60 else f"{h} after {s} s"
        for h, s in park.items()
    )
    # Cursor resumes by session/load on the same session id, which the probe
    # and the stop/resume test measured (the conversation survived).
    unmeasured = [h for h in park if h not in ("claude-code", "cursor")]
    if unmeasured:
        return Finding(
            "warn",
            "park",
            f"{text}; how {', '.join(unmeasured)} resumes has not been measured",
        )
    return Finding("ok", "park", text)


def _network(snap: Snapshot) -> Finding:
    if snap.network_error:
        return Finding("error", "network", snap.network_error)
    n = snap.network
    if not n.enabled:
        return Finding("ok", "network", "no network readings")
    speed = (
        "no speed test"
        if not n.speed_interval
        else f"a {n.speed_bytes / 1e6:g} MB speed test every {n.speed_interval / 60:g} min"
    )
    return Finding(
        "ok",
        "network",
        f"egress every {n.interval:g} s, exit IP every {n.trace_interval / 60:g} min, "
        f"{speed}, while a browser watches",
    )


def _exit(snap: Snapshot, roots, multi_user: bool) -> list[Finding]:
    """An exit that cannot carry anything is an error: what names it fails on
    purpose, with a 502, rather than leaving from this machine's address."""
    names = {snap.exit_via} | {a.exit for a in snap.agents if a.exit != "off"}
    names.discard(None)
    names.discard("")
    if not names:
        return []
    if multi_user:
        return [Finding("error", "exit_via", "an exit is off on a multi-user server")]
    links = {
        e.get("name"): e for e in LinkStore(roots.state_root / "links.json").load()
    }
    out = []
    for name in sorted(names):
        link = links.get(name)
        if link is None:
            out.append(Finding("error", "exit_via", f"{name} is not a link here"))
        elif not link.get("trusted"):
            out.append(
                Finding("error", "exit_via", f"the link to {name} is not trusted")
            )
        else:
            out.append(
                Finding("ok", "exit_via", f"what aegis starts exits through {name}")
            )
    return out


def _workflows(w: dict, agents: tuple[str, ...] = ()) -> Finding:
    if "error" in w:
        return Finding("error", "workflows", w["error"])
    m = w["max_agents"]
    unknown = [k for k in m if k != "total" and k not in agents]
    if unknown:
        return Finding(
            "error",
            "workflows",
            f"max_agents names {', '.join(unknown)}, not an agent in agents:",
        )
    above = [f"{k} {n}" for k, n in m.items() if k != "total" and n > m["total"]]
    if above:
        return Finding(
            "error",
            "workflows",
            f"max_agents {', '.join(above)} is above total {m['total']}",
        )
    each = "".join(f", {n} {k}" for k, n in m.items() if k != "total")
    return Finding(
        "ok", "workflows", f"at most {m['total']} workflow node sessions at once{each}"
    )


def _session_memory(snap: Snapshot) -> Finding:
    if snap.session_memory_error:
        return Finding("error", "session_memory_max", snap.session_memory_error)
    cap = parse_size(snap.session_memory_max, total_ram())
    shown = "no cap" if cap is None else f"{cap / 1024**3:.1f} GB"
    return Finding(
        "ok",
        "session_memory_max",
        f"{snap.session_memory_max} ({shown}) per session, where the unit "
        "delegates its cgroup",
    )


def _terminal(name: str, p: dict, root: Path) -> Finding:
    where = f"terminal.{name}"
    if "error" in p:
        return Finding("error", where, p["error"])
    try:
        word = shlex.split(p["cmd"])[0]
    except (ValueError, IndexError):
        return Finding("error", where, f"cmd {p['cmd']!r} is not a command line")
    cwd = (root / p["cwd"]).resolve()
    if not cwd.is_dir():
        return Finding("error", where, f"cwd {p['cwd']} is not a directory")
    if shutil.which(word) is None:
        return Finding("warn", where, f"{word} is not on PATH; sh -c may still run it")
    restart = "" if p["restart"] == "no" else f", restart {p['restart']}"
    return Finding("ok", where, f"{p['cmd']} in {cwd}{restart}")


def _state(roots: Roots) -> Finding:
    sr = roots.state_root
    if found := legacy_state(sr):
        return Finding(
            "error",
            "state",
            f"{sr} holds aegis's pre-2.0 state ({', '.join(found)}); move it: mv {sr} {sr.parent / 'legacy-state'}",
        )
    probe = next(p for p in (sr, *sr.parents) if p.exists())
    if not os.access(probe, os.W_OK):
        return Finding(
            "error", "state", f"{probe} is not writable; aegis keeps its state in {sr}"
        )
    return Finding("ok", "state", str(sr))


def _blocked_for_others(path: Path) -> Path | None:
    """The first path that keeps a user other than its owner from running
    ``path``: a directory above it without o+x, or the file without o+x (and
    o+r for a script). None when every user can run it."""
    real = path.resolve()
    for d in reversed(real.parents):
        if not d.stat().st_mode & 0o001:
            return d
    mode = real.stat().st_mode
    try:
        script = real.read_bytes()[:2] == b"#!"
    except OSError:
        script = False
    if not mode & 0o001 or (script and not mode & 0o004):
        return real
    return None


def _multi_user(
    roots: Roots,
    mu: users.MultiUser,
    entries: list[dict],
    unit: dict[str, str] | None,
    bins: dict[str, str],
    machine: str | None = None,
) -> list[Finding]:
    """The multi-user mode's rows: the machine, the unit's capabilities, each
    member token's Linux user, the state directory, the pool file, and
    whether every user can run each harness. Reads only."""
    out = []
    machine = machine or platform.machine()
    if machine not in users.SETGROUPS:
        # as_user.py opens a person's files with a per-thread setgroups,
        # whose syscall number is known for these machines only.
        out.append(
            Finding(
                "error",
                "multi_user.files",
                f"multi_user supports {' and '.join(users.SETGROUPS)}; "
                f"this machine is {machine}",
            )
        )
    if unit is None:
        out.append(
            Finding("warn", "multi_user.unit", "no installed unit runs aegis serve")
        )
    else:
        missing = [
            c
            for key in ("AmbientCapabilities", "CapabilityBoundingSet")
            for c in users.CAPS
            if c not in unit.get(key, "").split()
        ]
        if missing:
            out.append(
                Finding(
                    "error",
                    "multi_user.unit",
                    "the unit lacks capabilities; its [Service] needs "
                    + " and ".join(users.UNIT_LINES),
                )
            )
        else:
            out.append(
                Finding("ok", "multi_user.unit", "the unit grants the capabilities")
            )
    me = os.getuid()
    members = 0
    for e in entries:
        if not isinstance(e, dict) or e.get("role") == "admin":
            continue
        members += 1
        name, user = e.get("name"), e.get("linux_user")
        where = f"multi_user.tokens.{name}"
        if not user:
            out.append(
                Finding(
                    "error",
                    where,
                    "no Linux user; revoke it and add it again with --linux-user",
                )
            )
            continue
        try:
            users.allowed(str(user), server_uid=me)
        except OpError as err:
            out.append(Finding("error", where, err.message))
    if not any(f.where.startswith("multi_user.tokens.") for f in out):
        out.append(
            Finding(
                "ok",
                "multi_user.tokens",
                f"{members} member tokens, each with a Linux user",
            )
        )
    try:
        st = roots.state_root.stat()
        if st.st_uid != me or st.st_mode & 0o777 != 0o700:
            out.append(
                Finding(
                    "error",
                    "multi_user.state",
                    f"{roots.state_root} must be mode 0700, owned by the server's user",
                )
            )
        else:
            out.append(Finding("ok", "multi_user.state", f"{roots.state_root}, 0700"))
    except FileNotFoundError:
        out.append(
            Finding(
                "ok", "multi_user.state", f"{roots.state_root} is made 0700 at start"
            )
        )
    if mu.env_file is not None:
        try:
            pst = mu.env_file.stat()
            if pst.st_uid != me or pst.st_mode & 0o077:
                out.append(
                    Finding(
                        "error",
                        "multi_user.env_file",
                        f"{mu.env_file} must be mode 0600, owned by the server's user",
                    )
                )
            else:
                n = len(users.parse_env(mu.env_file.read_text()))
                out.append(
                    Finding(
                        "ok", "multi_user.env_file", f"{mu.env_file}, {n} variables"
                    )
                )
        except OSError as err:
            out.append(
                Finding(
                    "error", "multi_user.env_file", f"{mu.env_file}: {err.strerror}"
                )
            )
    blocked = []
    for name, b in bins.items():
        exe = shutil.which(b)
        if exe is None:
            continue
        if (at := _blocked_for_others(Path(exe))) is not None:
            blocked.append(f"{name} at {exe}: {at} keeps other users out")
    out += [Finding("error", "multi_user.harnesses", b) for b in blocked]
    if not blocked:
        out.append(
            Finding("ok", "multi_user.harnesses", "every user can run each harness")
        )
    return out


def _installed_unit() -> dict[str, str] | None:
    """The [Service] settings of the first installed unit that runs aegis
    serve, drop-ins included; None when there is none."""
    from . import service

    found = service.units(service.UNIT_DIRS)
    if not found:
        return None
    return service.directives(service.cat(*found[0]))


def _token_entries(roots: Roots) -> list[dict]:
    """tokens.json's entries, read without a TokenStore, which would create
    the admin's token: the doctor writes nothing."""
    try:
        doc = json.loads((roots.state_root / "tokens.json").read_text())
    except (OSError, ValueError):
        return []
    entries = doc.get("tokens") if isinstance(doc, dict) else None
    return entries if isinstance(entries, list) else []


async def doctor(
    roots: Roots, bins: dict[str, str], start: Path | None = None
) -> list[Finding]:
    path = roots.config_root / CONFIG_FILE
    if not path.is_file():
        return [
            Finding(
                "error",
                "file",
                f"no {CONFIG_FILE} at {roots.config_root}; run `aegis init` there",
            ),
            _state(roots),
        ]
    data, error = load(path)
    if data is None:
        return [Finding("error", "file", f"does not parse: {error}"), _state(roots)]
    walked = start is not None and start.resolve() != roots.config_root
    out = [
        Finding("ok", "file", f"{path}, found from {start}" if walked else str(path))
    ]
    out += _unknown_keys(data)
    snap = Snapshot.parse(path, None, data)
    used = {a.harness for a in snap.agents if a.harness in HARNESSES}
    accepted = snap.harness_versions["accepted"]
    found = {
        f.harness: f
        for f in await detect(roots.config_root, bins, used, accepted=accepted)
    }
    for name, f in found.items():
        where = f"harness.{name}"
        if f.verdict and f.verdict["state"] in OUTSIDE:
            out.append(Finding("warn", where, f.verdict["message"]))
        if f.error:
            out.append(Finding("error", where, f.error))
        elif not (f.verdict and f.verdict["state"] in OUTSIDE):
            out.append(
                Finding("ok", where, f"{f.bin}, {f.version}, {len(f.models)} models")
            )
    if any(a.harness == "claude-code" for a in snap.agents):
        if (token := _claude_token(time.time())) is not None:
            out.append(token)
    by_name = {a.name: a for a in snap.agents}
    for a in snap.agents:
        out += _agent(a, found.get(a.harness))
    out.append(_default(snap.default_agent, by_name))
    out += [_queue(name, q, by_name) for name, q in snap.queues.items()]
    if snap.terminals_error:
        out.append(Finding("error", "terminals", snap.terminals_error))
    out += [_terminal(n, p, roots.config_root) for n, p in snap.terminals.items()]
    if snap.terminal_memory_error:
        out.append(Finding("error", "terminal_memory_max", snap.terminal_memory_error))
    out += _recap(roots)
    out.append(_context(snap.context))
    if "error" in snap.usage:
        out.append(Finding("error", "usage", snap.usage["error"]))
    out += _notices(snap.notices)
    if "error" in snap.nudges:
        out.append(Finding("error", "nudges", snap.nudges["error"]))
    out.append(_park(snap.park))
    if "error" in snap.harness_versions:
        out.append(Finding("error", "harness_versions", snap.harness_versions["error"]))
    out.append(_network(snap))
    out += _exit(snap, roots, multi_user=snap.multi_user is not None)
    if snap.host_error:
        out.append(Finding("error", "host", snap.host_error))
    out.append(_workflows(snap.workflows, tuple(a.name for a in snap.agents)))
    out.append(_session_memory(snap))
    out.append(_state(roots))
    out += _knowledge(roots)
    out += _embeddings(snap.knowledge)
    if snap.multi_user_error:
        out.append(Finding("error", "multi_user", snap.multi_user_error))
    elif snap.multi_user is not None:
        out += _multi_user(
            roots,
            snap.multi_user,
            _token_entries(roots),
            _installed_unit(),
            {h: bins.get(h, h) for h in used},
        )
    return out


def _claude_token(now: float) -> Finding | None:
    """The token Claude Code sessions sign in with (claude/login.py): None
    when there is no file, so the stored sign-in is in use."""
    path = login.token_path()
    if not path.exists():
        return None
    if why := login.file_problem(path):
        return Finding("error", "claude.token", why)
    when = login.expiry(path)
    if when is None:
        return Finding(
            "error", "claude.token", f"{path} does not hold a Claude Code token"
        )
    written, expires = when
    day = lambda t: time.strftime("%Y-%m-%d", time.gmtime(t))  # noqa: E731
    if expires - now < login.WARN_S:
        return Finding(
            "warn",
            "claude.token",
            f"{path} expires about {day(expires)}: run claude setup-token "
            "and paste the new token into the sign-in card or the file",
        )
    return Finding(
        "ok",
        "claude.token",
        f"{path}, written {day(written)}, expires about {day(expires)}",
    )


def _knowledge(roots: Roots) -> list[Finding]:
    """A warning when someone keeps skills or memories and git is missing:
    every save and delete is a commit, so each one would fail."""
    kn = roots.state_root / "knowledge"
    if not kn.is_dir() or not any(kn.iterdir()) or shutil.which("git"):
        return []
    return [
        Finding(
            "warn",
            "knowledge",
            "git is not on PATH: saving or deleting a skill or memory fails"
            " until it is installed",
        )
    ]


def _embeddings(block: dict) -> list[Finding]:
    """The knowledge: block. A model named without the extra installed is a
    warning, not an error: search goes on with BM25 alone."""
    if "error" in block:
        return [Finding("error", "knowledge", block["error"])]
    model = block["embeddings"]
    if model == "off":
        return []
    if not embed.available():
        return [
            Finding(
                "warn",
                "knowledge.embeddings",
                f"{model} is set but fastembed is not installed, so search uses"
                " keywords alone; install aegis-harness[embeddings]",
            )
        ]
    return [Finding("ok", "knowledge.embeddings", f"{model}, fused with BM25")]


def render(findings: Iterable[Finding]) -> str:
    """The findings as ``aegis doctor`` prints them, a count on the last line."""
    findings = list(findings)
    lines = [f"{MARK[f.level]} {f.where:<26} {f.message}" for f in findings]
    errors = sum(f.level == "error" for f in findings)
    warns = sum(f.level == "warn" for f in findings)
    return "\n".join([*lines, f"{errors} errors, {warns} warnings"])
