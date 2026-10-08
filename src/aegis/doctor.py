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
import os
import shutil
import tempfile
from asyncio.subprocess import PIPE, STDOUT
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .agents import EFFORTS, HARNESSES, Agent
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
from .roots import CONFIG_FILE, Roots, legacy_state
from .session import SpawnSpec

VERSION_TIMEOUT_S = 5.0
PROBE_TIMEOUT_S = 20.0
LABELS = {"claude-code": "Claude Code", "opencode": "OpenCode"}


@dataclass(frozen=True)
class Found:
    harness: str
    bin: str | None  # absolute, or None when not on PATH
    version: str | None = None
    models: tuple[Model, ...] = ()
    error: str | None = None

    def wire(self) -> dict:
        return {
            "harness": self.harness,
            "label": LABELS[self.harness],
            "bin": self.bin,
            "version": self.version,
            "models": [m.wire() for m in self.models],
            "error": self.error,
        }


async def _version(bin: str) -> str:
    proc = await asyncio.create_subprocess_exec(bin, "--version", stdout=PIPE, stderr=STDOUT)
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
    h = harness_for(name, bins["claude-code"], bins["opencode"])
    spec = SpawnSpec(agent="doctor", model="", effort="", permission="read", cwd=cwd, harness=name)
    with tempfile.TemporaryDirectory() as d:
        stderr = Path(d) / "stderr.log"
        try:
            return await asyncio.wait_for(h.probe(spec, stderr), PROBE_TIMEOUT_S)
        except Exception as e:
            tail = stderr.read_text(errors="replace").strip().splitlines()[-3:] if stderr.exists() else []
            raise RuntimeError("; ".join([str(e) or type(e).__name__, *tail])) from e


async def _find(name: str, bins: dict[str, str], cwd: Path) -> Found:
    bin = shutil.which(bins[name])
    if bin is None:
        return Found(name, None, error=f"{bins[name]} is not on PATH")
    try:
        version = await _version(bin)
    except (OSError, RuntimeError) as e:
        return Found(name, bin, error=f"`{bin} --version` failed: {e}")
    try:
        cat = await _catalog(name, {**bins, name: bin}, cwd)
    except RuntimeError as e:
        return Found(name, bin, version, error=f"{LABELS[name]} gave no model list: {e}")
    return Found(name, bin, version, cat.models)


async def detect(cwd: Path, bins: dict[str, str], only: Iterable[str] = HARNESSES) -> list[Found]:
    wanted = set(only)
    return list(await asyncio.gather(*(_find(h, bins, cwd) for h in HARNESSES if h in wanted)))


def propose(found: list[Found]) -> ConfigDoc:
    have = {f.harness: f for f in found if f.bin}
    agents: list[AgentDoc] = []
    if "claude-code" in have:
        agents.append(AgentDoc(name="opus", harness="claude-code", model="opus", effort="high", permission="full"))
    oc = have.get("opencode")
    if oc is not None and oc.models:
        first = oc.models[0]
        name = first.value.rsplit("/", 1)[-1]
        if any(a.name == name for a in agents):
            name += "-opencode"
        offered = [e for e in first.efforts if e in EFFORTS]
        effort = "high" if "high" in offered or not offered else offered[0]
        agents.append(AgentDoc(name=name, harness="opencode", model=first.value, effort=effort, permission="full"))
    if not agents:
        return ConfigDoc()
    default = agents[0].name
    return ConfigDoc(
        agents=agents,
        default_agent=default,
        queues=[QueueDoc(name="general", agent=default, max_parallel=3)],
    )


def _unknown_keys(data: dict) -> list[Finding]:
    out = [
        Finding("warn", str(k), f"aegis does not read {k!r}; it reads {', '.join(TOP_KEYS)}")
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
                Finding("warn", f"{row}.{k}", f"aegis does not read {k!r}; it reads {', '.join(known)}", row)
                for k in keys
                if k not in known
            ]
    return out


def _agent(a: Agent, f: Found | None) -> list[Finding]:
    row = f"agents.{a.name}"
    if a.error:
        return [Finding("error", row, a.error, row)]
    if a.harness not in HARNESSES:
        return [Finding("error", f"{row}.harness", f"aegis runs {', '.join(HARNESSES)}, not {a.harness!r}", row)]
    if f is not None and f.error:
        return [Finding("error", f"{row}.harness", f"{LABELS[a.harness]} cannot run here: {f.error}", row)]
    out: list[Finding] = []
    if f is not None and f.models:
        m = next((m for m in f.models if a.model in (m.value, m.resolved)), None)
        if m is None:
            listed = ", ".join(x.value for x in f.models[:8])
            out.append(Finding("warn", f"{row}.model", f"{LABELS[a.harness]} does not list {a.model!r}; it lists {listed}", row))
        elif m.efforts and a.effort not in m.efforts:
            out.append(Finding("warn", f"{row}.effort", f"{m.label} takes {', '.join(m.efforts)}, not {a.effort!r}", row))
    return out or [Finding("ok", row, f"{a.harness} {a.model}, effort {a.effort}, permission {a.permission}", row)]


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
        return Finding("error", f"{row}.agent", f"no agent named {q['agent']!r}", row)
    if a.error:
        return Finding("error", f"{row}.agent", f"agent {a.name!r} cannot spawn: {a.error}", row)
    return Finding("ok", row, f"{a.name}, {q['max_parallel']} at a time", row)


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
        return Finding("error", "state", f"{probe} is not writable; aegis keeps its state in {sr}")
    return Finding("ok", "state", str(sr))


async def doctor(roots: Roots, bins: dict[str, str], start: Path | None = None) -> list[Finding]:
    path = roots.config_root / CONFIG_FILE
    if not path.is_file():
        return [
            Finding("error", "file", f"no {CONFIG_FILE} at {roots.config_root}; run `aegis init` there"),
            _state(roots),
        ]
    data, error = load(path)
    if data is None:
        return [Finding("error", "file", f"does not parse: {error}"), _state(roots)]
    walked = start is not None and start.resolve() != roots.config_root
    out = [Finding("ok", "file", f"{path}, found from {start}" if walked else str(path))]
    out += _unknown_keys(data)
    snap = Snapshot.parse(path, None, data)
    used = {a.harness for a in snap.agents if a.harness in HARNESSES}
    found = {f.harness: f for f in await detect(roots.config_root, bins, used)}
    for name, f in found.items():
        where = f"harness.{name}"
        if f.error:
            out.append(Finding("error", where, f.error))
        else:
            out.append(Finding("ok", where, f"{f.bin}, {f.version}, {len(f.models)} models"))
    by_name = {a.name: a for a in snap.agents}
    for a in snap.agents:
        out += _agent(a, found.get(a.harness))
    out.append(_default(snap.default_agent, by_name))
    out += [_queue(name, q, by_name) for name, q in snap.queues.items()]
    out.append(_state(roots))
    return out
