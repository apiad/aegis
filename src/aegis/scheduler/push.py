"""Receiver-side schedule push: validate, write atomically with provenance.

Also hosts shared read-side helpers (list/show/remove/logs payload
builders) reused by both the HTTP plane and the MCP server, so the two
layers stay 1:1 without one importing from the other.
"""
from __future__ import annotations

import io
import json
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from aegis.scheduler.cron import next_fire as _validate_cron


@dataclass
class RemoveResult:
    """Outcome of ``remove_schedule``.

    ``status`` is one of ``"ok"``, ``"not_found"``, ``"wrong_source"``.
    ``source`` is set only when ``status == "wrong_source"`` (the actual
    classification, e.g. ``"inline"`` or ``"overlay"``).
    """
    status: str
    source: str | None = None


def validate_spec(spec: dict, *, workflow_registry) -> None:
    """Raise ValueError with a clear message on invalid spec."""
    if not isinstance(spec, dict):
        raise ValueError("spec must be a JSON object")
    workflow = spec.get("workflow")
    if workflow is None:
        raise ValueError("spec.workflow is required")
    if workflow_registry.get(workflow) is None:
        raise ValueError(f"unknown workflow: {workflow!r}")

    if "cron" in spec:
        try:
            _validate_cron(spec["cron"], spec.get("timezone", "UTC"),
                           datetime.now(timezone.utc))
        except ValueError as e:
            raise ValueError(f"invalid cron: {e}")
    elif "fire_at" in spec:
        try:
            datetime.fromisoformat(spec["fire_at"].replace("Z", "+00:00"))
        except (ValueError, TypeError, AttributeError) as e:
            raise ValueError(f"invalid fire_at: {e}")
    else:
        raise ValueError("spec must have 'cron' or 'fire_at'")

    if workflow == "enqueue" and spec.get("args", {}).get("callback"):
        raise ValueError(
            "callback=true on a scheduled remote enqueue is not allowed "
            "(scheduler has no inbox to deliver to)")

    lc = spec.get("lifecycle", "forever")
    if lc not in ("forever", "once") and not isinstance(lc, dict):
        raise ValueError(f"invalid lifecycle: {lc!r}")


def classify_source(file_path: Path | None, inline_names: set[str],
                    name: str) -> tuple[str, str | None, str | None]:
    """Return (source, pushed_from, pushed_at) for a schedule.

    source ∈ {"inline", "overlay", "pushed"}.
    pushed_from / pushed_at are None unless source == "pushed".
    """
    if name in inline_names:
        return ("inline", None, None)
    if file_path is None or not file_path.exists():
        return ("inline", None, None)
    first_two = file_path.read_text().splitlines()[:2]
    pf: str | None = None
    pa: str | None = None
    for line in first_two:
        if line.startswith("# pushed_from:"):
            rest = line[len("# pushed_from:"):].strip()
            if " at " in rest:
                pf, pa = rest.rsplit(" at ", 1)
            else:
                pf = rest
        elif line.startswith("# pushed_at:"):
            pa = line[len("# pushed_at:"):].strip()
    if pf is not None:
        return ("pushed", pf.strip(), (pa or "").strip())
    return ("overlay", None, None)


def write_atomic(state_root: Path, name: str, spec: dict,
                 pushed_from: str) -> Path:
    """Serialize spec to YAML with a provenance header; atomic rename
    into state_root/.aegis/schedules/<name>.yaml."""
    dest_dir = state_root / ".aegis" / "schedules"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{name}.yaml"

    out = dict(spec)
    if "cron" in out and isinstance(out["cron"], str):
        out["cron"] = DoubleQuotedScalarString(out["cron"])
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.default_flow_style = False
    buf = io.StringIO()
    yaml.dump(out, buf)
    serialized = buf.getvalue()

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    body = (f"# pushed_from: {pushed_from} at {now}\n"
            f"{serialized}")

    tmp = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=dest_dir, delete=False, suffix=".tmp")
    try:
        tmp.write(body)
        tmp.flush()
        Path(tmp.name).replace(dest)
    finally:
        tmp.close()
    return dest


# ── shared read-side helpers (HTTP plane + MCP server) ────────────────

def _schedule_file_path(state_root: Path, name: str) -> Path:
    return state_root / ".aegis" / "schedules" / f"{name}.yaml"


def _iso(dt):
    if dt is None:
        return None
    try:
        return dt.isoformat()
    except AttributeError:
        return dt


def list_payload(scheduler, state_root: Path,
                 inline_names: set[str]) -> dict:
    """Build the `{schedules: [...]}` payload — same shape as
    `GET /remote/v1/schedule`."""
    if scheduler is None:
        return {"schedules": []}
    rows = []
    for entry in scheduler.snapshot():
        source, _, _ = classify_source(
            _schedule_file_path(state_root, entry.name), inline_names,
            entry.name)
        rows.append({
            "name": entry.name,
            "source": source,
            "next_fire": _iso(entry.next_fire),
            "fire_count": entry.fire_count,
            "in_flight": entry.in_flight,
            "enabled": entry.enabled,
            "workflow": entry.spec.get("workflow"),
            "cron": entry.spec.get("cron"),
        })
    return {"schedules": rows}


def show_payload(scheduler, state_root: Path, inline_names: set[str],
                 name: str) -> dict | None:
    """Build the schedule-show payload; return None if unknown."""
    entry = scheduler.get(name) if scheduler is not None else None
    if entry is None:
        return None
    source, pf, pa = classify_source(
        _schedule_file_path(state_root, name), inline_names, name)
    return {
        "name": name,
        "source": source,
        "spec": entry.spec,
        "runtime": {
            "next_fire": _iso(entry.next_fire),
            "last_fire": _iso(entry.last_completed_at),
            "fire_count": entry.fire_count,
            "in_flight": entry.in_flight,
            "enabled": entry.enabled,
        },
        "pushed_from": pf,
        "pushed_at": pa,
    }


def remove_schedule(scheduler, state_root: Path, inline_names: set[str],
                    name: str) -> RemoveResult:
    """Attempt to remove a pushed schedule."""
    entry = scheduler.get(name) if scheduler is not None else None
    if entry is None:
        return RemoveResult(status="not_found")
    file_path = _schedule_file_path(state_root, name)
    source, _, _ = classify_source(file_path, inline_names, name)
    if source != "pushed":
        return RemoveResult(status="wrong_source", source=source)
    file_path.unlink()
    return RemoveResult(status="ok")


def logs_payload(state_root: Path, name: str, *, tail: int = 50) -> dict:
    """Tail the schedule's JSONL log; empty list when the file is missing."""
    log_path = (state_root / ".aegis" / "state" / "schedules"
                / f"{name}.jsonl")
    if not log_path.exists():
        return {"records": []}
    lines = log_path.read_text().splitlines()[-tail:]
    records = []
    for line in lines:
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return {"records": records}
