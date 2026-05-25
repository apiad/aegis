"""Receiver-side schedule push: validate, write atomically with provenance."""
from __future__ import annotations

import io
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from aegis.scheduler.cron import next_fire as _validate_cron


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
