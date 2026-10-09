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

import html
import json
import re
import secrets
import shutil
from pathlib import Path

from .monitors import iso_now

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
    // wire the controls; answer with aegis.submit(data, label),
    // aegis.emit(name, data) or aegis.state(data)
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
    return len(json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode())


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
