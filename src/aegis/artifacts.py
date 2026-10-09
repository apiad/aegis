"""Artifacts: interactive pages an agent hands to the person.

The pure parts live here: the skeleton ``artifact_create`` writes, the static
checks a send runs before anything is served, the inbox text a page's answer
arrives under, and the caps. The per-session runtime (drafts, the latest
state, coalescing, probes, the error-per-turn cap) is ``Board`` below, held by
``Session.artifacts``. The operations are in ``artifact_ops.py``; the fold in
``transcript/entries.py``.

A page reaches the agent only through the inbox, so a click mid-turn is held
and lands when the turn ends, like a monitor's wake. State writes are
coalesced to one record a second, and always written before an event, a
submit, a close or an agent's update, so the fold sees every write that
mattered and the store is not a slider's firehose.
"""

from __future__ import annotations

import asyncio
import html
import json
import re
import secrets
import shutil
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .monitors import iso_now

if TYPE_CHECKING:
    from .session import Session

MAX_STATE_BYTES = 64 * 1024
LABEL_MAX = 140
TITLE_MAX = 140
EMITS_PER_MINUTE = 20
STATE_EVERY_S = 1.0
PROBE_TIMEOUT_S = 3.0
EVENTS_KEPT = 20
STACK_LINES = 5

SCRIPT_SRC = "/static/js/artifact.js"
STYLE_HREF = "/static/css/artifact.css"
ANSWERS = ("aegis.submit(", "aegis.emit(", "aegis.state(")
RESERVED = frozenset({"submit", "error", "close"})
_EVENT = re.compile(r"[a-z][a-z0-9_-]{0,31}")

SKELETON = """\
<!doctype html>
<html lang="en">
<meta charset="utf-8">
<title>{title}</title>
<link rel="stylesheet" href="/static/css/artifact.css">
<script src="/static/js/artifact.js"></script>
<body>
<h2>{title}</h2>
<!-- controls go here -->
<script>
  aegis.ready((state, theme) => {{
    // wire the controls; answer with aegis.submit / aegis.emit / aegis.state
  }});
</script>
</body>
</html>
"""


class ArtifactError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def skeleton(title: str) -> str:
    return SKELETON.format(title=html.escape(title))


def mint_id() -> str:
    return f"art-{secrets.token_hex(4)}"


def draft_dir(state_root: Path, id: str) -> Path:
    return state_root / "artifacts" / id


def draft_path(state_root: Path, id: str) -> Path:
    return draft_dir(state_root, id) / "index.html"


def write_draft(state_root: Path, id: str, title: str) -> Path:
    p = draft_path(state_root, id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(skeleton(title), encoding="utf-8")
    return p


def drop_draft(state_root: Path, id: str) -> None:
    shutil.rmtree(draft_dir(state_root, id), ignore_errors=True)


def drop_all_drafts(state_root: Path) -> None:
    """At boot: a draft never sent is gone with the process that held it."""
    shutil.rmtree(state_root / "artifacts", ignore_errors=True)


def check(text: str) -> None:
    """The two mistakes that leave a page that looks fine and never answers."""
    if SCRIPT_SRC not in text:
        raise ArtifactError(
            "no_script",
            f"the page does not load the aegis script; keep the skeleton's "
            f'<script src="{SCRIPT_SRC}"></script>',
        )
    if not any(a in text for a in ANSWERS):
        raise ArtifactError(
            "no_answer",
            "the page never answers: call aegis.submit(data, label), "
            "aegis.emit(name, data) or aegis.state(data) somewhere",
        )


def valid_event(name: str) -> bool:
    return bool(_EVENT.fullmatch(name)) and name not in RESERVED


def json_size(obj) -> int:
    return len(
        json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8", "surrogatepass"
        )
    )


def header(id: str, kind: str) -> str:
    return f"> from artifact:{id} · {kind} · {iso_now()}"


def body(data, label: str | None = None, error: tuple[str, str] | None = None) -> str:
    """The inbox body: the label, then the data as a JSON block; or an error's
    message and the first lines of its stack."""
    if error is not None:
        message, stack = error
        lines = [ln for ln in stack.splitlines() if ln.strip()][:STACK_LINES]
        return message + ("\n```\n" + "\n".join(lines) + "\n```" if lines else "")
    block = "```json\n" + json.dumps(data, ensure_ascii=False) + "\n```"
    return f"{label}\n{block}" if label else block


@dataclass
class Artifact:
    id: str
    title: str
    status: str = "draft"  # draft | live | submitted | closed
    file_id: str | None = None
    name: str = "index.html"
    caption: str | None = None
    state: Any = None
    state_by: str = "agent"
    started: bool | None = None
    events: list[dict] = field(default_factory=list)
    submitted: Any = None
    label: str | None = None
    dirty: bool = False  # a page's write not yet recorded
    last_written: float = 0.0
    emit_times: deque = field(default_factory=deque)
    errored_turn: int = -1


class Board:
    """One session's artifacts: drafts, the latest state of each live one,
    pending probes. Rebuilt from the fold's entries at load; drafts and probes
    do not survive the process."""

    def __init__(self, session: Session) -> None:
        self._s = session
        self.items: dict[str, Artifact] = {}
        self._timers: dict[str, asyncio.TimerHandle] = {}
        self._probes: dict[str, asyncio.Future] = {}

    # -- lookup --------------------------------------------------------------
    def get(self, id: str) -> Artifact:
        self._s.fold()  # boot reads no store: the first lookup loads the landed ones
        a = self.items.get(id)
        if a is None:
            raise ArtifactError("no_artifact", f"no artifact {id!r} in this session")
        return a

    def live(self, id: str) -> Artifact:
        a = self.get(id)
        if a.status != "live":
            raise ArtifactError("not_live", f"{id} is {a.status}")
        return a

    def all(self) -> list[Artifact]:
        self._s.fold()
        return list(self.items.values())

    def read(self, id: str) -> dict:
        a = self.get(id)
        return {
            "status": a.status,
            "state": a.state,
            "events": list(a.events),
            "submitted": a.submitted,
            "label": a.label,
        }

    # -- lifecycle -------------------------------------------------------------
    def create(self, title: str, caption: str | None, state: Any) -> Artifact:
        a = Artifact(id=mint_id(), title=title, caption=caption, state=state)
        write_draft(self._s.state_root, a.id, title)
        self.items[a.id] = a
        return a

    def land(
        self,
        a: Artifact,
        file_id: str,
        name: str,
        caption: str | None,
        started: bool | None,
    ) -> None:
        self.flush(a.id)
        a.status, a.file_id, a.name, a.caption, a.started = (
            "live",
            file_id,
            name,
            caption,
            started,
        )
        self._s.record_artifact(
            {
                "kind": "artifact",
                "artifact_id": a.id,
                "file_id": file_id,
                "name": name,
                "title": a.title,
                "caption": caption,
                "state": a.state,
                "started": started,
            }
        )

    def state(self, id: str, state: Any, by: str) -> None:
        a = self.live(id)
        a.state, a.state_by = state, by
        now = time.monotonic()
        if by == "agent" or now - a.last_written >= STATE_EVERY_S:
            self._write_state(a)
            return
        a.dirty = True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # no loop (a unit test): nothing to wait for
            self._write_state(a)
            return
        if id not in self._timers:
            self._timers[id] = loop.call_later(
                STATE_EVERY_S - (now - a.last_written), self.flush, id
            )

    def flush(self, id: str) -> None:
        t = self._timers.pop(id, None)
        if t is not None:
            t.cancel()
        a = self.items.get(id)
        if a is not None and a.dirty:
            self._write_state(a)

    def flush_all(self) -> None:
        for id in list(self.items):
            self.flush(id)

    def _write_state(self, a: Artifact) -> None:
        a.dirty = False
        a.last_written = time.monotonic()
        self._s.record_artifact(
            {
                "kind": "artifact_state",
                "artifact_id": a.id,
                "state": a.state,
                "by": a.state_by,
            }
        )

    def event(self, id: str, name: str, data: Any) -> None:
        a = self.live(id)
        if not valid_event(name):
            raise ArtifactError(
                "bad_name",
                f"{name!r}: one word, [a-z][a-z0-9_-]*, not submit/error/close",
            )
        now = time.monotonic()
        while a.emit_times and now - a.emit_times[0] > 60:
            a.emit_times.popleft()
        if len(a.emit_times) >= EMITS_PER_MINUTE:
            raise ArtifactError(
                "rate_limited", f"{EMITS_PER_MINUTE} events a minute at most"
            )
        a.emit_times.append(now)
        self.flush(id)
        a.events = (a.events + [{"name": name, "data": data, "ts": time.time()}])[
            -EVENTS_KEPT:
        ]
        self._s.record_artifact(
            {"kind": "artifact_event", "artifact_id": id, "name": name, "data": data}
        )

    def submit(self, id: str, data: Any, label: str) -> None:
        a = self.live(id)
        self.flush(id)
        a.status, a.submitted, a.label = "submitted", data, label
        self._s.record_artifact(
            {"kind": "artifact_submit", "artifact_id": id, "data": data, "label": label}
        )

    def close(self, id: str, label: str | None) -> None:
        a = self.get(id)
        if a.status == "draft":
            drop_draft(self._s.state_root, id)
            del self.items[id]
            return
        if a.status != "live":
            raise ArtifactError("not_live", f"{id} is {a.status}")
        self.flush(id)
        a.status, a.label = "closed", label
        self._s.record_artifact(
            {"kind": "artifact_close", "artifact_id": id, "label": label}
        )

    def error_wakes(self, id: str) -> bool:
        a = self.get(id)
        if a.errored_turn == self._s.turns:
            return False
        a.errored_turn = self._s.turns
        return True

    # -- probes --------------------------------------------------------------
    async def probe(self, a: Artifact, url: str, subscribers: int) -> bool | None:
        """Ask the browsers on this transcript to run the page hidden. True when
        one reports it started, None when none is open or none answered in
        time; raises ``page_error`` when one reports an error."""
        if subscribers <= 0:
            return None
        pid = f"probe-{secrets.token_hex(4)}"
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._probes[pid] = fut
        self._s.notify([{"probe": {"id": pid, "artifact_id": a.id, "url": url}}])
        try:
            started, message, stack = await asyncio.wait_for(fut, PROBE_TIMEOUT_S)
        except TimeoutError:
            return None
        finally:
            self._probes.pop(pid, None)
        if started:
            return True
        raise ArtifactError("page_error", body(None, error=(message, stack)))

    def probed(self, probe_id: str, started: bool, message: str, stack: str) -> bool:
        fut = self._probes.get(probe_id)
        if fut is None or fut.done():
            return False
        fut.set_result((started, message, stack))
        return True

    # -- load ----------------------------------------------------------------
    def load(self, entries: list[dict]) -> None:
        for e in entries:
            if e["kind"] != "artifact":
                continue
            d = e["detail"]
            self.items[e["id"]] = Artifact(
                id=e["id"],
                title=e["title"],
                status=e["status"],
                file_id=d.get("file_id"),
                caption=e.get("md"),
                state=d.get("state"),
                state_by=d.get("state_by") or "agent",
                started=d.get("started"),
                events=list(d.get("events") or []),
                submitted=d.get("submitted"),
                label=d.get("label"),
            )
