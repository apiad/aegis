# Artifacts Implementation Plan

**Status: planned, 2026-10-09.** Branch `feat/artifacts`, issue #217.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An agent calls `artifact_create`, edits the page aegis wrote, calls `artifact_send`; the page runs live in its transcript inside a sandboxed frame, the person's clicks reach the agent through the inbox, and a page that does not start never lands.

**Architecture:** A new module `src/aegis/artifacts.py` owns the pure parts (the skeleton, the static checks, the inbox text, the caps) and a per-session `Board` holding the runtime state (drafts, latest state, coalescing, probes, the error-per-turn cap). `src/aegis/artifact_ops.py` registers the five agent operations and the five person operations, like `config_ops.py`. The fold turns five new aegis records into one `artifact` entry keyed by the artifact id. The client gains a frame-side script (`client/js/artifact.js`), a stylesheet (`client/css/artifact.css`), a page-side bridge (`client/js/artifacts.js`) and an `artifact` renderer; `transcript.js` learns to update a row in place so a state write does not reload the frame.

**Tech Stack:** Python 3.13, Starlette, pydantic, fastmcp, vanilla ES modules (no build step), pytest, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-09-aegis-artifacts-design.md`

## Global Constraints

- Artifact id: `f"art-{secrets.token_hex(4)}"`; it is the entry id in the fold and the name in inbox headers.
- Draft path: `<state_root>/artifacts/<id>/index.html`; the directory is removed when a draft is closed unsent, never at boot (it is the working copy a resend reads).
- Caps, as module constants in `artifacts.py` that tests lower: `MAX_STATE_BYTES = 64 * 1024`, `LABEL_MAX = 140`, `EMITS_PER_MINUTE = 20`, `STATE_EVERY_S = 1.0`, `PROBE_TIMEOUT_S = 3.0`, `EVENTS_KEPT = 20`, `STACK_LINES = 5`, `TITLE_MAX = 140`.
- Event names: `re.compile(r"[a-z][a-z0-9_-]{0,31}")`, full match, never `submit`, `error` or `close`.
- Operation names: `artifact.create`, `artifact.send`, `artifact.read`, `artifact.update`, `artifact.close` (agents, `agent=True`); `artifact.state`, `artifact.emit`, `artifact.submit`, `artifact.error`, `artifact.probed` (people only).
- Error codes: `no_artifact`, `not_live`, `not_draft`, `no_script`, `no_answer`, `page_error`, `too_large`, `bad_name`, `rate_limited`, `bad_label`.
- Inbox header: `f"> from artifact:{id} · {kind} · {iso_now()}"` where `kind` is `submit`, the event name, or `error`; `iso_now` is `aegis.monitors.iso_now`.
- Records, all `src: aegis`: `artifact` (`artifact_id, file_id, name, title, caption, state, started`), `artifact_state` (`artifact_id, state, by`), `artifact_event` (`artifact_id, name, data`), `artifact_submit` (`artifact_id, data, label`), `artifact_close` (`artifact_id, label`).
- The frame is always `sandbox="allow-scripts"`; the served document always carries `Content-Security-Policy: sandbox allow-scripts` (already true for HTML in `files.headers`).
- `state` travels on the wire with the entry (it is capped at 64 KB and the frame needs it at mount); `events` are lazy detail.
- Page-side API: `aegis.ready(fn)`, `aegis.state(obj)`, `aegis.emit(name, obj)`, `aegis.submit(obj, label)`, `aegis.onState(fn)`.
- JSON-RPC methods: page→host `ui/initialize` (request), `aegis/state`, `aegis/emit`, `aegis/submit` (requests, so a refusal comes back as the error), `aegis/size`, `aegis/error` (notifications); host→page `aegis/state`, `aegis/theme`, `aegis/status` (notifications).
- Commits: conventional, English, named paths only (`git commit -- <paths>`), never amend. `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` on every commit.
- Run tests with `uv run pytest`; browser tests with `uv run pytest -q -m browser -k <name>`.

## Review Focus

1. A page that writes state on every `input` event of a slider: the frame must not reload and the store must get at most one record a second. Pinned in Task 3 (coalescing) and Task 6 (in-place update keeps the same iframe element).
2. A submit from a second browser while the first still shows the page live: the first's frame must get `aegis/status submitted` and refuse a second submit with `not_live`. Pinned in Task 4 (second submit refused) and Task 6 (status pushed on patch).
3. A page whose `ready` callback throws (the error comes after `ui/initialize`): the probe must still fail the send. Pinned in Task 6 (grace window test).
4. A `label` with a newline or over 140 characters, or `data` over 64 KB from the page: refused with a code the page can see, nothing reaches the agent. Pinned in Task 4.
5. A browser open on another tab of the same server, so the session's transcript has no subscriber: the send must not wait three seconds for nobody. Pinned in Task 4 (`started: null` returns at once when `subscribers == 0`).

---

### Task 1: `artifacts.py`, the pure parts

**Files:**
- Create: `src/aegis/artifacts.py`
- Test: `tests/test_artifacts.py`

**Interfaces:**
- Produces: `SKELETON: str` (a format template with `{title}`); `skeleton(title: str) -> str`; `mint_id() -> str`; `draft_dir(state_root: Path, id: str) -> Path`; `draft_path(state_root: Path, id: str) -> Path` (`draft_dir / "index.html"`); `write_draft(state_root, id, title) -> Path`; `check(text: str) -> None` raising `ArtifactError`; `valid_event(name: str) -> bool`; `json_size(obj) -> int`; `header(id: str, kind: str) -> str`; `body(data, label: str | None = None, error: tuple[str, str] | None = None) -> str`; `ArtifactError(code, message)` (same shape as `files.FileError`); the constants in Global Constraints.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_artifacts.py
import json
import re

import pytest

from aegis import artifacts
from aegis.artifacts import ArtifactError, body, check, header, mint_id, skeleton


def test_the_skeleton_loads_the_script_and_the_stylesheet_and_calls_ready():
    html = skeleton("Pick a layout")
    assert '<script src="/static/js/artifact.js"></script>' in html
    assert '<link rel="stylesheet" href="/static/css/artifact.css">' in html
    assert "<title>Pick a layout</title>" in html and "<h2>Pick a layout</h2>" in html
    assert "aegis.ready(" in html
    with pytest.raises(ArtifactError) as e:
        check(html)  # unedited, the skeleton answers nothing, and the check says so
    assert e.value.code == "no_answer"


def test_a_title_is_escaped_in_the_skeleton():
    assert "&lt;b&gt;" in skeleton("<b>") and "<b>" not in skeleton("<b>").split("<body>")[1]


def test_ids_are_art_dash_eight_hex():
    assert re.fullmatch(r"art-[0-9a-f]{8}", mint_id())
    assert mint_id() != mint_id()


def test_write_draft_puts_the_skeleton_under_the_state_root(tmp_path):
    p = artifacts.write_draft(tmp_path, "art-00000001", "Hi")
    assert p == tmp_path / "artifacts" / "art-00000001" / "index.html"
    assert p.read_text() == skeleton("Hi")


def test_check_refuses_a_page_without_the_script_or_without_an_answer():
    with pytest.raises(ArtifactError) as e:
        check("<h1>hi</h1><script>aegis.submit({})</script>")
    assert e.value.code == "no_script" and "/static/js/artifact.js" in e.value.message
    with pytest.raises(ArtifactError) as e:
        check('<script src="/static/js/artifact.js"></script><h1>hi</h1>')
    assert e.value.code == "no_answer" and "aegis.submit" in e.value.message
    for call in ("aegis.submit(", "aegis.emit(", "aegis.state("):
        check(f'<script src="/static/js/artifact.js"></script><script>{call}1)</script>')


@pytest.mark.parametrize(
    "name,ok",
    [("pick", True), ("a-b_c9", True), ("submit", False), ("error", False), ("close", False),
     ("Pick", False), ("9a", False), ("a" * 33, False), ("", False)],
)
def test_event_names(name, ok):
    assert artifacts.valid_event(name) is ok


def test_json_size_counts_the_compact_encoding():
    assert artifacts.json_size({"a": [1, 2]}) == len(b'{"a":[1,2]}')


def test_the_header_and_body_formats():
    h = header("art-3f9a12c0", "submit")
    assert h.startswith("> from artifact:art-3f9a12c0 · submit · 20")
    assert body({"layout": "b"}, "Picked B") == 'Picked B\n```json\n{"layout": "b"}\n```'
    assert body({"n": 1}) == '```json\n{"n": 1}\n```'
    err = body(None, error=("ReferenceError: d3 is not defined", "a\nb\nc\nd\ne\nf\ng"))
    assert err == "ReferenceError: d3 is not defined\n```\na\nb\nc\nd\ne\n```"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q tests/test_artifacts.py`
Expected: FAIL, `ModuleNotFoundError: No module named 'aegis.artifacts'`.

- [ ] **Step 3: Write the module**

```python
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
            f'the page does not load the aegis script; keep the skeleton\'s '
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q tests/test_artifacts.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/artifacts.py tests/test_artifacts.py
git commit -F - -- src/aegis/artifacts.py tests/test_artifacts.py <<'EOF'
feat(artifacts): the skeleton, the static checks and the inbox text (#217)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
```

### Task 2: the `artifact` entry in the fold

**Files:**
- Modify: `src/aegis/transcript/entries.py` (`_own`, `activity`), `src/aegis/transcript/describe.py` (glyph), `src/aegis/transcript/wire.py` (`LAZY`), `src/aegis/session.py` (`_record`'s activity kinds), `src/aegis/agent_ops.py` (`_render`)
- Test: `tests/test_fold.py`, `tests/test_wire.py`

**Interfaces:**
- Consumes: the five records from Global Constraints.
- Produces: one entry per artifact, id = `artifact_id`, `kind: "artifact"`, `status` one of `live`, `submitted`, `closed`, `glyph: ARTIFACT_GLYPH` (`"▣"`), `title` = the record's title, `summary` = the status word, `md` = caption, `detail = {"artifact_id", "file_id", "url", "state", "state_by", "started", "events", "submitted", "label", "ended_ts"}`; `activity()` returns `showed <title>` when an artifact entry is the latest; `_render` gives `artifact: <title> (<status>)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_fold.py, appended
def test_an_artifact_folds_to_one_entry_that_its_later_records_update():
    r = Rec()
    r.own("artifact", artifact_id="art-aaaa0001", file_id="F1", name="index.html",
          title="Pick", caption="Pick one", state={"n": 0}, started=True)
    r.own("artifact_state", artifact_id="art-aaaa0001", state={"n": 1}, by="page")
    r.own("artifact_event", artifact_id="art-aaaa0001", name="hover", data={"n": 1})
    r.own("artifact_submit", artifact_id="art-aaaa0001", data={"pick": "b"}, label="Picked B")
    f, ops = run(r)
    (e,) = f.entries()
    assert e["id"] == "art-aaaa0001" and e["kind"] == "artifact"
    assert e["status"] == "submitted" and e["summary"] == "submitted"
    assert e["title"] == "Pick" and e["md"] == "Pick one" and e["glyph"] == "▣"
    d = e["detail"]
    assert d["url"] == "/files/F1/index.html" and d["started"] is True
    assert d["state"] == {"n": 1} and d["state_by"] == "page"
    assert d["events"] == [{"name": "hover", "data": {"n": 1}, "ts": 1002.0}]
    assert d["submitted"] == {"pick": "b"} and d["label"] == "Picked B"
    assert d["ended_ts"] == 1003.0 and e["rev"] == 3
    assert all(op["upsert"]["id"] == "art-aaaa0001" for step in ops for op in step)


def test_a_resend_swaps_the_file_and_keeps_the_rest_and_a_close_collapses():
    r = Rec()
    r.own("artifact", artifact_id="art-aaaa0002", file_id="F1", name="index.html",
          title="T", caption=None, state=None, started=None)
    r.own("artifact_state", artifact_id="art-aaaa0002", state={"k": 1}, by="agent")
    r.own("artifact", artifact_id="art-aaaa0002", file_id="F2", name="index.html",
          title="T", caption="now", state=None, started=True)
    r.own("artifact_close", artifact_id="art-aaaa0002", label=None)
    f, _ = run(r)
    (e,) = f.entries()
    assert e["detail"]["url"] == "/files/F2/index.html" and e["md"] == "now"
    assert e["detail"]["state"] == {"k": 1} and e["detail"]["state_by"] == "agent"
    assert e["status"] == "closed" and e["detail"]["label"] is None
    assert f.activity() == "showed T"


def test_events_keep_the_last_twenty_and_a_record_for_an_unknown_artifact_is_ignored():
    r = Rec()
    r.own("artifact_state", artifact_id="art-nope0000", state={}, by="page")
    r.own("artifact", artifact_id="art-aaaa0003", file_id="F", name="index.html",
          title="T", caption=None, state=None, started=None)
    for n in range(25):
        r.own("artifact_event", artifact_id="art-aaaa0003", name="tick", data=n)
    f, _ = run(r)
    (e,) = f.entries()
    assert [ev["data"] for ev in e["detail"]["events"]] == list(range(5, 25))
```

```python
# tests/test_wire.py, appended
def test_an_artifacts_records_rebuild_from_every_cut_and_events_are_lazy():
    r = Rec()
    r.own("send", text="go")
    r.echo("go")
    r.own("artifact", artifact_id="art-aaaa0009", file_id="F", name="index.html",
          title="T", caption="c", state={"n": 0}, started=True)
    for n in range(3):
        r.own("artifact_state", artifact_id="art-aaaa0009", state={"n": n}, by="page")
    r.own("artifact_event", artifact_id="art-aaaa0009", name="hover", data=1)
    r.own("artifact_submit", artifact_id="art-aaaa0009", data={"p": 1}, label="done")
    r.text("thanks")
    r.result()
    final = fold_records(r.records)
    want = final.snapshot()["entries"]
    for k in range(len(r.records) + 1):
        held = fold_records(r.records[:k]).snapshot()
        assert rebuild(held["entries"], final.snapshot(held["rev"])) == want, k
    art = next(e for e in want if e["kind"] == "artifact")
    assert "events" not in art["detail"] and art["detail"]["more"] is True
    assert art["detail"]["state"] == {"n": 2}  # state rides the wire
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q tests/test_fold.py tests/test_wire.py -k artifact`
Expected: FAIL, no entries folded (`ValueError: not enough values to unpack`).

- [ ] **Step 3: Implement**

In `describe.py`, after `FILE_GLYPH`:

```python
ARTIFACT_GLYPH = "▣"
```

In `wire.py`, `LAZY` gains:

```python
    "artifact": ("events",),
```

In `entries.py`, `_own`, after the `file` branch (before the final return):

```python
        if kind == "artifact":
            aid = str(rec.get("artifact_id"))
            old = self._entries.get(aid)
            det = dict(old["detail"]) if old else {
                "state": rec.get("state"),
                "state_by": "agent",
                "events": [],
                "submitted": None,
                "label": None,
                "ended_ts": None,
            }
            det.update(
                artifact_id=aid,
                file_id=rec.get("file_id"),
                url=files.url(str(rec.get("file_id")), str(rec.get("name"))),
                started=rec.get("started"),
            )
            status = old["status"] if old else "live"
            return self._upsert(
                _entry(
                    aid, "artifact", status, old["ts"] if old else ts, d.ARTIFACT_GLYPH,
                    title=str(rec.get("title") or ""), summary=status,
                    md=rec.get("caption"), detail=det,
                )
            )
        if kind in ("artifact_state", "artifact_event", "artifact_submit", "artifact_close"):
            e = self._entries.get(str(rec.get("artifact_id")))
            if e is None:
                return []  # a record for a page that never landed here
            det = dict(e["detail"])
            status = e["status"]
            if kind == "artifact_state":
                det["state"], det["state_by"] = rec.get("state"), rec.get("by") or "page"
            elif kind == "artifact_event":
                det["events"] = (det["events"] + [
                    {"name": rec.get("name"), "data": rec.get("data"), "ts": ts}
                ])[-EVENTS_KEPT:]
            elif kind == "artifact_submit":
                status = "submitted"
                det.update(submitted=rec.get("data"), label=rec.get("label"), ended_ts=ts)
            else:
                status = "closed"
                det.update(label=rec.get("label"), ended_ts=ts)
            return self._upsert({**e, "status": status, "summary": status, "detail": det})
```

with `from ..artifacts import EVENTS_KEPT` at the top of `entries.py` (check `tests/test_imports.py` still passes: the import is relative and `artifacts.py` imports nothing from the transcript package, so there is no cycle).

In `activity()`, after the `file` branch:

```python
            if e["kind"] == "artifact":
                return _cut(f"showed {e['title']}")
```

In `session.py` `_record`, the activity kinds tuple becomes `("user", "prose", "tool", "file", "artifact")`.

In `agent_ops.py` `_render`, before the final return:

```python
    if kind == "artifact":
        return f"artifact: {e['title']} ({e['status']})"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q tests/test_fold.py tests/test_wire.py tests/test_imports.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git commit -F - -- src/aegis/transcript/entries.py src/aegis/transcript/describe.py src/aegis/transcript/wire.py src/aegis/session.py src/aegis/agent_ops.py tests/test_fold.py tests/test_wire.py <<'EOF'
feat(transcript): an artifact entry, updated in place by its later records (#217)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
```

### Task 3: the session's `Board`

**Files:**
- Modify: `src/aegis/artifacts.py` (add `Artifact` and `Board`), `src/aegis/session.py` (`artifacts`, `turns`, `record_artifact`, `notify`, `fold()`, `shutdown()`, `_on_line`)
- Test: `tests/test_artifacts.py`

**Interfaces:**
- Consumes: `Session._record`, `Session.deliver`, `Session._publish`, `Session.channel`, `Session.fold()`.
- Produces: `Session.artifacts: Board`; `Session.turns: int` (results seen by this process); `Session.record_artifact(record: dict) -> None`; `Session.notify(ops: list[dict]) -> None` (an op on the transcript channel that is no entry). `Board` methods: `create(title, caption, state) -> Artifact`; `get(id) -> Artifact` (raises `ArtifactError("no_artifact")`); `live(id) -> Artifact` (raises `not_live`); `land(a, file_id, name, caption, started) -> None` (writes the `artifact` record, status `live`); `state(id, state, by) -> None`; `flush(id) -> None`; `flush_all() -> None`; `event(id, name, data) -> None` (raises `bad_name`, `rate_limited`; writes the record); `submit(id, data, label) -> None`; `close(id, label) -> None`; `error_wakes(id) -> bool`; `probe(a, url, subscribers: int) -> bool | None` (async); `probed(probe_id, started, message, stack) -> bool`; `load(entries) -> None`; `read(id) -> dict`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_artifacts.py, appended
import asyncio

from aegis.artifacts import Board
from aegis.transcript.store import read_store

from .test_session import Harness


def recs(h):
    return read_store(h.path)[0]


def art_entries(s):
    return [e for e in s.entries() if e["kind"] == "artifact"]


def test_a_draft_is_in_the_board_and_not_in_the_transcript(tmp_path, fake_claude):
    h = Harness(tmp_path, fake_claude)
    s = h.session
    a = s.artifacts.create("Pick", "cap", {"n": 0})
    assert a.status == "draft" and a.id.startswith("art-")
    assert artifacts.draft_path(s.state_root, a.id).read_text() == skeleton("Pick")
    assert art_entries(s) == [] and s.artifacts.read(a.id)["status"] == "draft"
    with pytest.raises(ArtifactError) as e:
        s.artifacts.live(a.id)
    assert e.value.code == "not_live"
    with pytest.raises(ArtifactError) as e:
        s.artifacts.get("art-ffffffff")
    assert e.value.code == "no_artifact"


async def test_state_writes_are_coalesced_to_one_record_a_second(tmp_path, fake_claude, monkeypatch):
    monkeypatch.setattr(artifacts, "STATE_EVERY_S", 0.2)
    h = Harness(tmp_path, fake_claude)
    s = h.session
    a = s.artifacts.create("T", None, None)
    s.artifacts.land(a, "F", "index.html", None, True)
    for n in range(5):
        s.artifacts.state(a.id, {"n": n}, "page")
    states = [r for r in h.records() if r.get("kind") == "artifact_state"]
    assert len(states) == 1 and states[0]["state"] == {"n": 0}  # the first at once
    assert s.artifacts.read(a.id)["state"] == {"n": 4}  # memory is never behind
    await asyncio.sleep(0.3)
    states = [r for r in h.records() if r.get("kind") == "artifact_state"]
    assert [x["state"] for x in states] == [{"n": 0}, {"n": 4}]  # then the latest, once
    s.artifacts.state(a.id, {"n": 5}, "page")
    s.artifacts.event(a.id, "hover", 1)  # an event flushes first
    kinds = [r["kind"] for r in h.records() if r["kind"].startswith("artifact")]
    assert kinds[-2:] == ["artifact_state", "artifact_event"]
    assert art_entries(s)[0]["detail"]["state"] == {"n": 5}
    assert h.refold_matches()


def test_an_agents_write_is_recorded_at_once_and_marked(tmp_path, fake_claude):
    h = Harness(tmp_path, fake_claude)
    s = h.session
    a = s.artifacts.create("T", None, None)
    s.artifacts.land(a, "F", "index.html", None, None)
    s.artifacts.state(a.id, {"x": 1}, "page")
    s.artifacts.state(a.id, {"x": 2}, "agent")
    states = [r for r in h.records() if r.get("kind") == "artifact_state"]
    assert [(x["state"], x["by"]) for x in states] == [({"x": 1}, "page"), ({"x": 2}, "agent")]


def test_events_are_rate_limited_and_names_checked(tmp_path, fake_claude, monkeypatch):
    monkeypatch.setattr(artifacts, "EMITS_PER_MINUTE", 2)
    h = Harness(tmp_path, fake_claude)
    s = h.session
    a = s.artifacts.create("T", None, None)
    s.artifacts.land(a, "F", "index.html", None, None)
    s.artifacts.event(a.id, "a", 1)
    s.artifacts.event(a.id, "b", 2)
    with pytest.raises(ArtifactError) as e:
        s.artifacts.event(a.id, "c", 3)
    assert e.value.code == "rate_limited"
    with pytest.raises(ArtifactError) as e:
        s.artifacts.event(a.id, "submit", 3)
    assert e.value.code == "bad_name"


def test_submit_and_close_end_the_artifact_and_a_second_is_refused(tmp_path, fake_claude):
    h = Harness(tmp_path, fake_claude)
    s = h.session
    a = s.artifacts.create("T", None, None)
    s.artifacts.land(a, "F", "index.html", None, None)
    s.artifacts.submit(a.id, {"p": "b"}, "Picked B")
    assert art_entries(s)[0]["status"] == "submitted"
    assert s.artifacts.read(a.id) == {
        "status": "submitted", "state": None, "events": [], "submitted": {"p": "b"}, "label": "Picked B",
    }
    with pytest.raises(ArtifactError) as e:
        s.artifacts.submit(a.id, {}, "again")
    assert e.value.code == "not_live"
    b = s.artifacts.create("U", None, None)
    s.artifacts.land(b, "G", "index.html", None, None)
    s.artifacts.close(b.id, None)
    assert art_entries(s)[1]["status"] == "closed"
    c = s.artifacts.create("V", None, None)
    s.artifacts.close(c.id, None)  # a draft: gone, nothing recorded
    assert not artifacts.draft_dir(s.state_root, c.id).exists()
    assert len(art_entries(s)) == 2
    with pytest.raises(ArtifactError):
        s.artifacts.get(c.id)


def test_one_error_wakes_per_artifact_per_turn(tmp_path, fake_claude):
    h = Harness(tmp_path, fake_claude)
    s = h.session
    a = s.artifacts.create("T", None, None)
    s.artifacts.land(a, "F", "index.html", None, None)
    assert s.artifacts.error_wakes(a.id) is True
    assert s.artifacts.error_wakes(a.id) is False
    s.turns += 1  # what _on_line does on a result
    assert s.artifacts.error_wakes(a.id) is True


def test_a_restart_reloads_landed_artifacts_from_the_store_and_forgets_drafts(tmp_path, fake_claude):
    h = Harness(tmp_path, fake_claude)
    s = h.session
    a = s.artifacts.create("T", "c", {"n": 0})
    s.artifacts.land(a, "F", "index.html", "c", True)
    s.artifacts.state(a.id, {"n": 9}, "agent")
    s.artifacts.create("draft", None, None)
    s2 = h.make()
    assert s2.artifacts.read(a.id)["state"] == {"n": 9}
    assert s2.artifacts.get(a.id).status == "live"
    assert [x.id for x in s2.artifacts.all()] == [a.id]


async def test_a_probe_answers_when_a_browser_reports_and_none_when_nobody_listens(tmp_path, fake_claude, monkeypatch):
    monkeypatch.setattr(artifacts, "PROBE_TIMEOUT_S", 0.3)
    h = Harness(tmp_path, fake_claude)
    s = h.session
    a = s.artifacts.create("T", None, None)
    assert await s.artifacts.probe(a, "/files/F/index.html", subscribers=0) is None
    published = []
    s._publish = lambda ch, ops: published.append((ch, ops))
    task = asyncio.ensure_future(s.artifacts.probe(a, "/files/F/index.html", subscribers=1))
    await asyncio.sleep(0)
    (ch, ops), = published
    assert ch == s.channel and ops[0]["probe"]["url"] == "/files/F/index.html"
    pid = ops[0]["probe"]["id"]
    assert ops[0]["probe"]["artifact_id"] == a.id
    assert s.artifacts.probed("nope", True, "", "") is False  # unknown probe: dropped
    assert s.artifacts.probed(pid, False, "ReferenceError: x", "at a:1\nat b:2") is True
    with pytest.raises(ArtifactError) as e:
        await task
    assert e.value.code == "page_error" and "ReferenceError: x" in e.value.message
    slow = asyncio.ensure_future(s.artifacts.probe(a, "/files/F/index.html", subscribers=1))
    assert await slow is None  # nobody answered within the timeout: lands unprobed
```

`Harness` in `tests/test_session.py` exposes `.session`, `.make()`, `.path` and `.refold_matches()`; its store is `<tmp>/state/transcripts/log-abc.jsonl`, so `Session.state_root` is `<tmp>/state`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q tests/test_artifacts.py`
Expected: FAIL, `AttributeError: 'Session' object has no attribute 'artifacts'`.

- [ ] **Step 3: Implement**

Append to `artifacts.py`:

```python
import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .session import Session


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
        self, a: Artifact, file_id: str, name: str, caption: str | None, started: bool | None
    ) -> None:
        self.flush(a.id)
        a.status, a.file_id, a.name, a.caption, a.started = "live", file_id, name, caption, started
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
            {"kind": "artifact_state", "artifact_id": a.id, "state": a.state, "by": a.state_by}
        )

    def event(self, id: str, name: str, data: Any) -> None:
        a = self.live(id)
        if not valid_event(name):
            raise ArtifactError("bad_name", f"{name!r}: one word, [a-z][a-z0-9_-]*, not submit/error/close")
        now = time.monotonic()
        while a.emit_times and now - a.emit_times[0] > 60:
            a.emit_times.popleft()
        if len(a.emit_times) >= EMITS_PER_MINUTE:
            raise ArtifactError("rate_limited", f"{EMITS_PER_MINUTE} events a minute at most")
        a.emit_times.append(now)
        self.flush(id)
        a.events = (a.events + [{"name": name, "data": data, "ts": time.time()}])[-EVENTS_KEPT:]
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
        self._s.record_artifact({"kind": "artifact_close", "artifact_id": id, "label": label})

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
```

In `session.py`:

- import `from .artifacts import Board`;
- in `__init__`, after `self.catalog_task = None`: `self.turns = 0` and `self.artifacts = Board(self)`;
- a property `state_root` returning `self.store.path.parents[1]` (the store is `<state>/transcripts/<log_id>.jsonl`), with the docstring "Where this session's drafts and sent files live.";
- in `fold()`, right after `self._reads_from = self._fold.rev`: `self.artifacts.load(self._fold.entries())`;
- `def record_artifact(self, record: dict) -> None: self._record(record)` next to `record_file`, docstring "A record from the session's artifacts board (artifacts.py).";
- `def notify(self, ops: list[dict]) -> None: self._publish(self.channel, ops)` with the docstring "An op on the transcript channel that is no entry: a probe request. It is not in any snapshot, so a browser that subscribes later never sees it.";
- in `_on_line`, in the `if any(isinstance(ev, Result) ...)` block, first line: `self.turns += 1`;
- in `shutdown()`, first line: `self.artifacts.flush_all()`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q tests/test_artifacts.py tests/test_session.py tests/test_no_cwd.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git commit -F - -- src/aegis/artifacts.py src/aegis/session.py tests/test_artifacts.py <<'EOF'
feat(artifacts): the session's board: drafts, coalesced state, probes, one error a turn (#217)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
```

### Task 4: the operations

**Files:**
- Create: `src/aegis/artifact_ops.py`
- Modify: `src/aegis/app.py` (`__init__`: `register_artifact_ops(self)`; `boot`: `drop_all_drafts`; module docstring's operation list)
- Test: `tests/test_artifacts_e2e.py`, `tests/test_agents.py` (the tool-name set at line 137)

**Interfaces:**
- Consumes: `Board`, `files.store`, `files.url`, `app.channels.subscribers`, `Session.deliver`, `own(caller)` (copy the three-line helper from `agent_ops.py`: it is local to `register_agent_ops`).
- Produces: the ten operations in Global Constraints. Agent results: `artifact.create -> {id, path, html}`; `artifact.send -> {id, url, started}`; `artifact.read -> {status, state, events, submitted, label}`; `artifact.update -> {id, url, started}`; `artifact.close -> "closed"`. Person results: `artifact.state -> "ok"`, `artifact.emit -> "ok"`, `artifact.submit -> "ok"`, `artifact.error -> "ok"` (or `"dropped"` when the cap held it), `artifact.probed -> "ok"` or `"dropped"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_artifacts_e2e.py
"""Artifacts end to end: the fake claude calls the real tools over /mcp, and
the person's side is called through the registry as the browser would."""

import asyncio
import json

import httpx
import pytest

from aegis import artifacts
from aegis.ops import Caller, OpError

from .conftest import until
from .test_agents import CONFIG, World, inbox, mcp, turn


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


def ok(said: str) -> dict:
    assert said.startswith("mcp ok: "), said
    return json.loads(said.removeprefix("mcp ok: "))


def arts(s):
    return [e for e in s.entries() if e["kind"] == "artifact"]


PAGE = (
    '<script src="/static/js/artifact.js"></script><button id="b">B</button>'
    '<script>aegis.ready(() => { b.onclick = () => aegis.submit({p: "b"}, "Picked B"); });</script>'
)


async def test_create_send_read_update_close(world, tmp_path):
    a = await world.spawn()
    made = ok(await turn(a, mcp("artifact_create", title="Pick", caption="Pick one", state={"n": 0})))
    assert made["id"].startswith("art-") and made["html"] == artifacts.skeleton("Pick")
    path = tmp_path / ".aegis" / "state" / "artifacts" / made["id"] / "index.html"
    assert made["path"] == str(path) and path.read_text() == made["html"]
    assert arts(a) == []  # a draft is not in the transcript

    path.write_text(PAGE)
    sent = ok(await turn(a, mcp("artifact_send", id=made["id"])))
    assert sent["started"] is None and sent["url"].startswith("/files/")  # nobody is watching
    (e,) = arts(a)
    assert e["status"] == "live" and e["md"] == "Pick one" and e["detail"]["state"] == {"n": 0}
    async with httpx.AsyncClient() as c:
        r = await c.get(world.base + sent["url"])
    assert r.text == PAGE and r.headers["content-security-policy"] == "sandbox allow-scripts"

    assert ok(await turn(a, mcp("artifact_read", id=made["id"]))) == {
        "status": "live", "state": {"n": 0}, "events": [], "submitted": None, "label": None,
    }
    upd = ok(await turn(a, mcp("artifact_update", id=made["id"], state={"n": 2}, caption="now")))
    assert upd["url"] == sent["url"]
    assert arts(a)[0]["detail"]["state"] == {"n": 2} and arts(a)[0]["md"] == "now"

    path.write_text(PAGE.replace("B</button>", "C</button>"))
    re = ok(await turn(a, mcp("artifact_update", id=made["id"], resend=True)))
    assert re["url"] != sent["url"] and arts(a)[0]["detail"]["url"] == re["url"]
    assert arts(a)[0]["detail"]["state"] == {"n": 2}  # carried over

    assert await turn(a, mcp("artifact_close", id=made["id"], label="never mind")) == "mcp ok: closed"
    assert arts(a)[0]["status"] == "closed" and arts(a)[0]["detail"]["label"] == "never mind"
    said = await turn(a, mcp("artifact_update", id=made["id"], state={}))
    assert said.startswith("mcp error: not_live")


async def test_a_page_that_cannot_answer_is_refused_and_nothing_lands(world, tmp_path):
    a = await world.spawn()
    made = ok(await turn(a, mcp("artifact_create", title="T")))
    path = tmp_path / ".aegis" / "state" / "artifacts" / made["id"] / "index.html"
    path.write_text("<h1>hi</h1>")
    said = await turn(a, mcp("artifact_send", id=made["id"]))
    assert said.startswith("mcp error: no_script") and arts(a) == []
    path.write_text('<script src="/static/js/artifact.js"></script><h1>hi</h1>')
    said = await turn(a, mcp("artifact_send", id=made["id"]))
    assert said.startswith("mcp error: no_answer") and arts(a) == []
    said = await turn(a, mcp("artifact_send", id="art-00000000"))
    assert said.startswith("mcp error: no_artifact")


async def test_a_failed_probe_deletes_the_snapshot_and_lands_nothing(world, tmp_path, monkeypatch):
    monkeypatch.setattr(artifacts, "PROBE_TIMEOUT_S", 2.0)
    a = await world.spawn()
    made = ok(await turn(a, mcp("artifact_create", title="T")))
    (tmp_path / ".aegis" / "state" / "artifacts" / made["id"] / "index.html").write_text(PAGE)
    probes = []
    original = world.app.channels.publish

    def publish(channel, ops):
        probes.extend(op["probe"] for op in ops if "probe" in op)
        original(channel, ops)

    world.app.channels.publish = publish
    # A subscriber, as a browser with the tab open would be.
    world.app.channels.subscribe(a.channel, lambda msg: None)
    await a.send(mcp("artifact_send", id=made["id"]))
    await until(lambda: probes, what="the probe request")
    (p,) = probes
    assert p["artifact_id"] == made["id"] and p["url"].startswith("/files/")
    async with httpx.AsyncClient() as c:
        assert (await c.get(world.base + p["url"])).status_code == 200
    await world.app.registry.call(
        "artifact.probed",
        {"log_id": a.log_id, "probe_id": p["id"], "started": False,
         "message": "ReferenceError: d3 is not defined", "stack": "at index.html:14"},
    )
    await until(lambda: a.status == "idle", what="the turn")
    said = [e["md"] for e in a.entries() if e["kind"] == "prose"][-1]
    assert said.startswith("mcp error: page_error") and "d3 is not defined" in said and "index.html:14" in said
    assert arts(a) == []
    async with httpx.AsyncClient() as c:
        assert (await c.get(world.base + p["url"])).status_code == 404  # the snapshot is gone
    assert await world.app.registry.call(
        "artifact.probed", {"log_id": a.log_id, "probe_id": p["id"], "started": True}
    ) == "dropped"


async def landed(world, tmp_path, page=PAGE):
    a = await world.spawn()
    made = ok(await turn(a, mcp("artifact_create", title="T")))
    (tmp_path / ".aegis" / "state" / "artifacts" / made["id"] / "index.html").write_text(page)
    ok(await turn(a, mcp("artifact_send", id=made["id"])))
    return a, made["id"]


async def test_a_submit_during_a_resend_probe_wins_and_the_new_page_is_dropped(world, tmp_path, monkeypatch):
    monkeypatch.setattr(artifacts, "PROBE_TIMEOUT_S", 2.0)
    a, aid = await landed(world, tmp_path)
    (tmp_path / ".aegis" / "state" / "artifacts" / aid / "index.html").write_text(PAGE.replace("B<", "C<"))
    world.app.channels.subscribe(a.channel, lambda msg: None)  # a browser, so the probe waits
    await a.send(mcp("artifact_update", id=aid, resend=True))
    await until(lambda: a.status == "working", what="the resend")
    await asyncio.sleep(0.2)
    await world.app.registry.call("artifact.submit", {"log_id": a.log_id, "artifact_id": aid, "data": {"p": "b"}, "label": "Picked B"})
    await until(lambda: a.status == "idle" and len(inbox(a)) == 1, timeout=10, what="the late submit and the refused resend")
    said = [e["md"] for e in a.entries() if e["kind"] == "prose"][-2]
    assert said.startswith("mcp error: not_live"), said
    (e,) = arts(a)
    assert e["status"] == "submitted"
    assert len([p for p in (tmp_path / ".aegis" / "state" / "files").iterdir()]) == 1  # the new snapshot is gone


async def test_a_submit_wakes_the_agent_and_a_second_is_refused(world, tmp_path):
    a, aid = await landed(world, tmp_path)
    reg = world.app.registry
    await reg.call("artifact.state", {"log_id": a.log_id, "artifact_id": aid, "state": {"n": 1}})
    await reg.call("artifact.submit", {"log_id": a.log_id, "artifact_id": aid, "data": {"p": "b"}, "label": "Picked B"})
    await until(lambda: inbox(a) and a.status == "idle", what="the wake")
    (m,) = inbox(a)
    assert m["title"].startswith(f"artifact:{aid} · submit")
    assert 'Picked B\n```json\n{"p": "b"}\n```' in m["md"]
    assert arts(a)[0]["status"] == "submitted" and arts(a)[0]["detail"]["state"] == {"n": 1}
    with pytest.raises(OpError) as e:
        await reg.call("artifact.submit", {"log_id": a.log_id, "artifact_id": aid, "data": {}, "label": "x"})
    assert e.value.code == "not_live"
    with pytest.raises(OpError) as e:
        await reg.call("artifact.state", {"log_id": a.log_id, "artifact_id": aid, "state": {}})
    assert e.value.code == "not_live"


async def test_an_emit_wakes_the_agent_and_leaves_the_page_live(world, tmp_path):
    a, aid = await landed(world, tmp_path)
    await world.app.registry.call(
        "artifact.emit", {"log_id": a.log_id, "artifact_id": aid, "name": "hover", "data": [1, 2]}
    )
    await until(lambda: inbox(a) and a.status == "idle", what="the wake")
    assert inbox(a)[0]["title"].startswith(f"artifact:{aid} · hover") and "[1, 2]" in inbox(a)[0]["md"]
    assert arts(a)[0]["status"] == "live"
    assert ok(await turn(a, mcp("artifact_read", id=aid)))["events"][0]["name"] == "hover"


async def test_caps_are_enforced_before_anything_reaches_the_agent(world, tmp_path, monkeypatch):
    monkeypatch.setattr(artifacts, "MAX_STATE_BYTES", 32)
    a, aid = await landed(world, tmp_path)
    reg = world.app.registry
    for op, extra in (("artifact.state", {"state": "x" * 40}), ("artifact.emit", {"name": "e", "data": "x" * 40})):
        with pytest.raises(OpError) as e:
            await reg.call(op, {"log_id": a.log_id, "artifact_id": aid, **extra})
        assert e.value.code == "too_large"
    with pytest.raises(OpError) as e:
        await reg.call("artifact.emit", {"log_id": a.log_id, "artifact_id": aid, "name": "Bad Name"})
    assert e.value.code == "bad_name"
    with pytest.raises(OpError) as e:
        await reg.call("artifact.submit", {"log_id": a.log_id, "artifact_id": aid, "label": "a\nb"})
    assert e.value.code == "bad_params"
    with pytest.raises(OpError) as e:
        await reg.call("artifact.state", {"log_id": a.log_id, "artifact_id": "art-00000000", "state": 1})
    assert e.value.code == "no_artifact"
    with pytest.raises(OpError) as e:
        await reg.call("artifact.state", {"log_id": a.log_id, "artifact_id": aid, "state": 1}, Caller("agent", a.log_id))
    assert e.value.code == "not_for_agents"
    assert inbox(a) == []


async def test_errors_wake_once_per_turn(world, tmp_path):
    a, aid = await landed(world, tmp_path)
    reg = world.app.registry
    assert await reg.call("artifact.error", {"log_id": a.log_id, "artifact_id": aid, "message": "boom", "stack": "at x:1"}) == "ok"
    assert await reg.call("artifact.error", {"log_id": a.log_id, "artifact_id": aid, "message": "boom2", "stack": ""}) == "dropped"
    await until(lambda: inbox(a) and a.status == "idle", what="the wake")
    (m,) = inbox(a)
    assert m["title"].startswith(f"artifact:{aid} · error") and "boom\n```\nat x:1\n```" in m["md"]
    assert await reg.call("artifact.error", {"log_id": a.log_id, "artifact_id": aid, "message": "boom3", "stack": ""}) == "ok"


async def test_a_restart_forgets_drafts_but_a_live_page_can_still_be_resent(world, tmp_path):
    a, aid = await landed(world, tmp_path)
    draft = ok(await turn(a, mcp("artifact_create", title="D")))
    await world.restart()
    a = world.session(a.log_id)
    said = await turn(a, mcp("artifact_send", id=draft["id"]))
    assert said.startswith("mcp error: no_artifact")  # the board forgot the draft
    (tmp_path / ".aegis" / "state" / "artifacts" / aid / "index.html").write_text(PAGE.replace("B<", "Z<"))
    re = ok(await turn(a, mcp("artifact_update", id=aid, resend=True)))  # its working copy survived
    assert arts(a)[0]["detail"]["url"] == re["url"]
```

In `tests/test_agents.py`, the set asserted by `test_the_tools_are_named_after_their_operations_and_take_no_handle` gains `"artifact_create", "artifact_send", "artifact_read", "artifact_update", "artifact_close"`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q tests/test_artifacts_e2e.py tests/test_agents.py -k "artifact or named_after"`
Expected: FAIL, `mcp error: unknown tool` / the name-set assertion.

- [ ] **Step 3: Implement**

```python
"""The artifact operations: five for agents, five for the page's bridge.

An agent creates a draft, edits it with its own tools, and sends it; the send
runs the static checks, snapshots the draft like ``file.send`` does, asks the
browsers on the transcript to run it hidden (``Board.probe``), and records the
artifact only when the page started or nobody was there to try. The page's
side is five person operations the bridge (``client/js/artifacts.js``) calls
with the artifact id the host holds, never one the page sent.
"""

from __future__ import annotations

import asyncio
import shutil
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from . import artifacts, files
from .artifacts import ArtifactError, Board
from .ops import Caller, OpError

if TYPE_CHECKING:
    from .app import App


class _Strict(BaseModel):
    model_config = {"extra": "forbid"}


Label = Field(min_length=1, max_length=artifacts.LABEL_MAX, pattern=r"^[^\n]+$")


class ArtifactCreate(_Strict):
    title: str = Field(min_length=1, max_length=artifacts.TITLE_MAX, pattern=r"^[^\n]+$",
                       description="The page's heading and title.")
    caption: str | None = Field(None, description="One line of Markdown shown above the card.")
    state: Any = Field(None, description="JSON the page receives on init; up to 64 KB.")


class ArtifactId(_Strict):
    id: str


class ArtifactUpdate(_Strict):
    id: str
    state: Any = Field(None, description="New state, pushed into the live page.")
    caption: str | None = None
    resend: bool = Field(False, description="Snapshot the edited draft again and swap the page.")


class ArtifactClose(_Strict):
    id: str
    label: str | None = Field(None, max_length=artifacts.LABEL_MAX, pattern=r"^[^\n]+$")


class PageRef(_Strict):
    log_id: str
    artifact_id: str


class PageState(PageRef):
    state: Any = None


class PageEmit(PageRef):
    name: str
    data: Any = None


class PageSubmit(PageRef):
    data: Any = None
    label: str = Label


class PageError(PageRef):
    message: str
    stack: str = ""


class Probed(_Strict):
    log_id: str
    probe_id: str
    started: bool
    message: str = ""
    stack: str = ""


def _err(e: ArtifactError) -> OpError:
    return OpError(e.code, e.message)


def _sized(obj: Any, what: str) -> None:
    n = artifacts.json_size(obj)
    if n > artifacts.MAX_STATE_BYTES:
        raise OpError("too_large", f"{what} is {n} bytes; the limit is {artifacts.MAX_STATE_BYTES}")


def register_artifact_ops(app: App) -> None:
    r, reg = app.registry, app.sessions

    def own(caller: Caller):
        if not caller.is_agent or caller.log_id not in reg.sessions:
            raise OpError("agents_only", "only an agent's session can do this")
        return reg.sessions[caller.log_id]

    async def land(s, a, caption: str | None):
        """Checks, snapshot, probe, record; the snapshot goes when the probe fails."""
        path = artifacts.draft_path(s.state_root, a.id)
        try:
            artifacts.check(path.read_text(encoding="utf-8"))
        except OSError as e:
            raise OpError("unreadable", f"cannot read the draft {path}: {e}") from e
        except ArtifactError as e:
            raise _err(e) from e
        try:
            rec = await asyncio.to_thread(files.store, app.roots.state_root, path)
        except files.FileError as e:
            raise OpError(e.code, e.message) from e
        url = files.url(rec["file_id"], rec["name"])
        landed = False
        try:
            started = await s.artifacts.probe(a, url, app.channels.subscribers(s.channel))
            if a.status not in ("draft", "live"):
                # The person answered the old page while the probe ran.
                raise ArtifactError("not_live", f"{a.id} was {a.status} while the new page was probed")
            s.artifacts.land(a, rec["file_id"], rec["name"], caption, started)
            landed = True
        except ArtifactError as e:
            raise _err(e) from e
        finally:
            if not landed:  # a failed probe, a late submit, an interrupt: nothing is served
                shutil.rmtree(app.roots.state_root / "files" / rec["file_id"], ignore_errors=True)
        return {"id": a.id, "url": url, "started": started}

    # -- agents ------------------------------------------------------------------
    @r.op("artifact.create", ArtifactCreate, agent=True)
    async def create(p: ArtifactCreate, caller):
        """Start an interactive page for the person: a decision to make, values
        to tune, a form, a visualization to play with. Writes a working
        skeleton and returns its path and text; edit that file with your Edit
        tool, then artifact_send. The page talks to aegis through
        `window.aegis`: `aegis.ready((state, theme) => ...)` runs once the host
        has handed over the state and the theme's CSS variables;
        `aegis.state(obj)` keeps state silently for you to artifact_read;
        `aegis.emit(name, obj)` wakes you and leaves the page live;
        `aegis.submit(obj, label)` wakes you with the answer and closes the
        page, the card collapsing to `label`; `aegis.onState(fn)` hears your
        artifact_update. Answers reach you as a user turn headed
        `> from artifact:<id> · submit|<event>|error · …` with the JSON in a
        code block. Rules the checks cannot catch: an event name is one word,
        `[a-z][a-z0-9_-]{0,31}`, never `submit`, `error` or `close`; state,
        event data and the submit's data are at most 64 KB each; at most 20
        emits a minute; a submit's label is one line of at most 140
        characters. Keep the skeleton's script tag and stylesheet link, or
        drop the stylesheet for your own look."""
        s = own(caller)
        if p.state is not None:
            _sized(p.state, "state")
        a = s.artifacts.create(p.title, p.caption, p.state)
        path = artifacts.draft_path(s.state_root, a.id)
        return {"id": a.id, "path": str(path), "html": path.read_text(encoding="utf-8")}

    @r.op("artifact.send", ArtifactId, agent=True)
    async def send(p: ArtifactId, caller):
        """Land the draft in your transcript. The page is checked and run
        hidden in the person's browser first: a page that throws is refused
        with `page_error` and the message, and nothing is shown, so edit the
        draft and send again. Only then write your message, which may refer to
        the card above it, and end the turn with turn_end(needs_you) when the
        page asks something."""
        s = own(caller)
        try:
            a = s.artifacts.get(p.id)
        except ArtifactError as e:
            raise _err(e) from e
        if a.status != "draft":
            raise OpError("not_draft", f"{p.id} is {a.status}; artifact_update with resend to swap a live page")
        return await land(s, a, a.caption)

    @r.op("artifact.read", ArtifactId, agent=True)
    async def read(p: ArtifactId, caller):
        """The page's status (draft, live, submitted, closed), its latest state,
        its last 20 events, and the submit's data and label. Wakes nobody."""
        s = own(caller)
        try:
            return s.artifacts.read(p.id)
        except ArtifactError as e:
            raise _err(e) from e

    @r.op("artifact.update", ArtifactUpdate, agent=True)
    async def update(p: ArtifactUpdate, caller):
        """Change a live page: push `state` into it, change its caption, or
        with `resend` snapshot the edited draft again and swap the page (the
        state carries over unless `state` is also given)."""
        s = own(caller)
        if p.state is None and p.caption is None and not p.resend:
            raise OpError("bad_params", "nothing to change")
        if p.state is not None:
            _sized(p.state, "state")  # before a resend swaps the page
        try:
            a = s.artifacts.live(p.id)
            if p.resend:
                out = await land(s, a, p.caption if p.caption is not None else a.caption)
            elif p.caption is not None:
                s.artifacts.land(a, a.file_id, a.name, p.caption, a.started)
                out = {"id": a.id, "url": files.url(a.file_id, a.name), "started": a.started}
            else:
                out = {"id": a.id, "url": files.url(a.file_id, a.name), "started": a.started}
            if p.state is not None:
                s.artifacts.state(a.id, p.state, "agent")
        except ArtifactError as e:
            raise _err(e) from e
        return out

    @r.op("artifact.close", ArtifactClose, agent=True)
    async def close(p: ArtifactClose, caller):
        """Withdraw a page; the card collapses to `label`. A draft never sent
        is simply discarded."""
        s = own(caller)
        try:
            s.artifacts.close(p.id, p.label)
        except ArtifactError as e:
            raise _err(e) from e
        return "closed"

    # -- the page, through the bridge ----------------------------------------------
    def page(p: PageRef):
        s = reg.open(p.log_id)
        try:
            s.artifacts.get(p.artifact_id)
        except ArtifactError as e:
            raise _err(e) from e
        return s

    @r.op("artifact.state", PageState)
    async def state(p: PageState, caller):
        s = page(p)
        _sized(p.state, "state")
        try:
            s.artifacts.state(p.artifact_id, p.state, "page")
        except ArtifactError as e:
            raise _err(e) from e
        return "ok"

    @r.op("artifact.emit", PageEmit)
    async def emit(p: PageEmit, caller):
        s = page(p)
        _sized(p.data, "data")
        try:
            s.artifacts.event(p.artifact_id, p.name, p.data)
        except ArtifactError as e:
            raise _err(e) from e
        await s.deliver(artifacts.header(p.artifact_id, p.name), artifacts.body(p.data))
        return "ok"

    @r.op("artifact.submit", PageSubmit)
    async def submit(p: PageSubmit, caller):
        s = page(p)
        _sized(p.data, "data")
        try:
            s.artifacts.submit(p.artifact_id, p.data, p.label)
        except ArtifactError as e:
            raise _err(e) from e
        await s.deliver(artifacts.header(p.artifact_id, "submit"), artifacts.body(p.data, p.label))
        return "ok"

    @r.op("artifact.error", PageError)
    async def error(p: PageError, caller):
        s = page(p)
        if not s.artifacts.error_wakes(p.artifact_id):
            return "dropped"
        await s.deliver(
            artifacts.header(p.artifact_id, "error"),
            artifacts.body(None, error=(p.message, p.stack)),
        )
        return "ok"

    @r.op("artifact.probed", Probed)
    async def probed(p: Probed, caller):
        s = reg.open(p.log_id)
        return "ok" if s.artifacts.probed(p.probe_id, p.started, p.message, p.stack) else "dropped"
```

In `app.py`: `from .artifact_ops import register_artifact_ops`; in `__init__` after `register_config_ops(self)`: `register_artifact_ops(self)`; add the ten names to the module docstring's operation list. Boot does not touch `<state>/artifacts/`: it holds the working copy of every landed page, which a resend after a restart reads; a draft never sent is simply absent from the board after a restart (its file stays, a few KB, until the folder is cleaned by hand).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q tests/test_artifacts_e2e.py tests/test_agents.py tests/test_ops.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git commit -F - -- src/aegis/artifact_ops.py src/aegis/app.py tests/test_artifacts_e2e.py tests/test_agents.py <<'EOF'
feat(artifacts): artifact_create/send/read/update/close for agents, and the page's five operations (#217)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
```

### Task 5: the frame script and the stylesheet

**Files:**
- Create: `src/aegis/client/js/artifact.js`, `src/aegis/client/css/artifact.css`
- Test: `tests/test_web.py` (served with the right types), `tests/test_client_rules.py` (already globs `js/*.js`; the new file must pass it)

**Interfaces:**
- Produces: `window.aegis` with `ready(fn)`, `state(obj)`, `emit(name, obj)`, `submit(obj, label)`, `onState(fn)`; the JSON-RPC messages in Global Constraints; `document.documentElement.dataset.status` set to the artifact's status; the theme variables set on `:root`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_web.py, appended
def test_the_artifact_script_and_stylesheet_are_served(project, fake_claude):
    c = client_for(project, fake_claude)
    js = c.get("/static/js/artifact.js")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]
    assert "ui/initialize" in js.text and "aegis/submit" in js.text
    css = c.get("/static/css/artifact.css")
    assert css.status_code == 200 and "text/css" in css.headers["content-type"]
    assert "var(--accent)" in css.text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest -q tests/test_web.py -k artifact_script`
Expected: FAIL, 404.

- [ ] **Step 3: Write the script and the stylesheet**

`src/aegis/client/js/artifact.js` (a classic script, not a module: agents' pages include it with a plain `<script src>`):

```js
// The page's side of an aegis artifact. Included by the page aegis wrote
// (artifact_create's skeleton). Talks to the host page by JSON-RPC 2.0 over
// postMessage; the host is the parent window, and the target is "*" because
// this frame's origin is opaque (CSP sandbox) and it cannot know the host's.
// With no host (the raw document opened in its own tab) ready() fires with an
// empty state and the other calls only log.
(() => {
  const host = window.parent !== window ? window.parent : null;
  let seq = 0;
  let status = "live";
  let init = null; // {state, theme} once the host answered
  const readyFns = [];
  const stateFns = [];
  let pending; // the latest state() argument not yet posted
  let raf = 0;

  const post = (m) => host && host.postMessage(m, "*");
  const request = (method, params) => post({ jsonrpc: "2.0", id: ++seq, method, params });
  const notify = (method, params) => post({ jsonrpc: "2.0", method, params });
  const warn = (what) => console.warn(`aegis.${what}: this artifact is ${status}`);

  function applyTheme(vars) {
    for (const [k, v] of Object.entries(vars || {})) document.documentElement.style.setProperty(k, v);
  }
  function setStatus(s) {
    status = s;
    document.documentElement.dataset.status = s;
  }
  function fire(state, theme) {
    if (init) return;
    init = { state, theme };
    applyTheme(theme);
    for (const fn of readyFns.splice(0)) fn(state, theme);
  }

  window.addEventListener("message", (ev) => {
    if (host && ev.source !== host) return;
    const m = ev.data;
    if (!m || m.jsonrpc !== "2.0") return;
    if (m.id !== undefined && m.result && m.result.artifact !== undefined) {
      setStatus(m.result.status === "probe" ? "live" : m.result.status);
      fire(m.result.state ?? {}, m.result.theme || {});
    } else if (m.id !== undefined && m.error) {
      console.warn(`aegis: ${m.error.code || ""} ${m.error.message || ""}`.trim());
    } else if (m.method === "aegis/state") {
      for (const fn of stateFns) fn(m.params.state);
    } else if (m.method === "aegis/theme") {
      applyTheme(m.params.theme);
    } else if (m.method === "aegis/status") {
      setStatus(m.params.status);
    }
  });

  window.aegis = {
    ready(fn) {
      if (init) fn(init.state, init.theme);
      else readyFns.push(fn);
    },
    onState(fn) {
      stateFns.push(fn);
    },
    state(obj) {
      if (status !== "live") return warn("state");
      pending = obj;
      if (!raf) raf = requestAnimationFrame(() => { raf = 0; request("aegis/state", { state: pending }); });
    },
    emit(name, data) {
      if (status !== "live") return warn("emit");
      request("aegis/emit", { name, data: data ?? null });
    },
    submit(data, label) {
      if (status !== "live") return warn("submit");
      request("aegis/submit", { data: data ?? null, label: String(label || "answered") });
    },
  };

  const errorOf = (message, stack) => notify("aegis/error", { message: String(message), stack: String(stack || "") });
  window.addEventListener("error", (ev) => errorOf(ev.message, ev.error?.stack || `${ev.filename}:${ev.lineno}`));
  window.addEventListener("unhandledrejection", (ev) => errorOf(ev.reason?.message || ev.reason, ev.reason?.stack));

  const size = () => notify("aegis/size", { height: document.documentElement.scrollHeight });
  document.addEventListener("DOMContentLoaded", () => {
    new ResizeObserver(size).observe(document.documentElement);
    size();
    // After the inline scripts ran, so an error in them is reported before the
    // handshake and a probe sees it first.
    if (host) request("ui/initialize", {});
    else fire({}, {});
  });
})();
```

`src/aegis/client/css/artifact.css`, optional for a page, reads only the theme variables and falls back to a plain look when opened raw:

```css
/* The look of an artifact that keeps the skeleton's stylesheet: the theme's
   variables, handed in by js/artifact.js, with fallbacks for a page opened
   on its own. A page that wants its own look drops the link. */
:root{--bg:#fff;--ink:#1a1a1a;--accent:#2f5ba8;--on-accent:#fff;--surface:#f6f6f4;--raised:#eeeeea;--rule:#d9d9d4;--muted:#666;--r:6px;
  --font-ui:system-ui,sans-serif;--font-mono:ui-monospace,monospace}
html,body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 var(--font-ui)}
body{padding:14px 16px}
h1,h2,h3{margin:0 0 10px;font-weight:600;line-height:1.25}
h2{font-size:16px}
p{margin:0 0 10px}
code,pre{font-family:var(--font-mono);font-size:12.5px}
button{font:inherit;padding:7px 14px;border:1px solid var(--accent);border-radius:var(--r);background:var(--accent);color:var(--on-accent);cursor:pointer}
button:hover{filter:brightness(1.08)}
button.secondary{background:transparent;color:var(--accent)}
button[disabled]{opacity:.5;cursor:default}
label{display:block;margin:6px 0 2px;color:var(--muted);font-size:12.5px}
input,textarea{font:inherit;color:var(--ink);background:var(--surface);border:1px solid var(--rule);border-radius:var(--r);padding:6px 8px;box-sizing:border-box}
input[type=range]{padding:0;accent-color:var(--accent);background:none;border:0;width:100%}
input[type=checkbox],input[type=radio]{accent-color:var(--accent)}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:8px 0}
.card{border:1px solid var(--rule);border-radius:var(--r);padding:12px;background:var(--surface);margin:8px 0}
.card.selected{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent) inset}
html[data-status=submitted] body,html[data-status=closed] body{opacity:.6;pointer-events:none}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q tests/test_web.py tests/test_client_rules.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git commit -F - -- src/aegis/client/js/artifact.js src/aegis/client/css/artifact.css tests/test_web.py <<'EOF'
feat(client): the artifact page script and its theme stylesheet (#217)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
```

### Task 6: the bridge, the card and the in-place update

**Files:**
- Create: `src/aegis/client/js/artifacts.js`
- Modify: `src/aegis/client/js/entries.js` (`artifact` renderer, `update` export), `src/aegis/client/js/transcript.js` (`apply`: update in place), `src/aegis/client/js/app.js` (wire the bridge, route `probe` ops, theme switch, Show click), `src/aegis/client/index.html` (a hidden `<div id="probes" hidden></div>` before the app script), `src/aegis/client/css/base.css` (`.acard` rules next to `.fcard`)
- Test: `tests/test_browser.py`

**Interfaces:**
- Consumes: `callFor(key, op, params)` and `shown` in `app.js`; `transcript.entries`; `conn.subscribe` handler for `transcript:<log_id>`.
- Produces: `artifacts.js` exports `setup({ call, entry })`, `probe(req)`, `theme()`; `entries.js` exports `update(entry, node) -> boolean`; a row `.row.artifact` with `.acard[data-status]`, `iframe[data-artifact=<id>][sandbox="allow-scripts"]` while live, `.done` line and `button.show` after; the `probe` op on the transcript channel handled before `transcript.apply`.

- [ ] **Step 1: Write the failing browser tests**

```python
# tests/test_browser.py, appended after the sent-file tests
SKELETON_WITH = (
    '<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="/static/css/artifact.css">'
    '<script src="/static/js/artifact.js"></script><body><h2>Pick</h2>{body}</body>'
)


def _artifact(page, server, body, n, title="Pick", caption=None):
    """Create a draft through the fake claude, write ``body`` into it, send it.
    ``n`` is how many turns the page has seen done; two more happen here.
    Returns the artifact id and the send's last prose line."""
    args = {"title": title} | ({"caption": caption} if caption else {})
    page.fill("#input", f"/mcp artifact_create {json.dumps(args)}")
    page.press("#input", "Enter")
    turns_done(page, n + 1)
    made = json.loads(page.locator(".row.prose .md").last.inner_text().removeprefix("mcp ok: "))
    Path(made["path"]).write_text(SKELETON_WITH.format(body=body))
    page.fill("#input", f"/mcp artifact_send {json.dumps({'id': made['id']})}")
    page.press("#input", "Enter")
    turns_done(page, n + 2)
    return made["id"], page.locator(".row.prose .md").last.inner_text()


PICK = (
    '<button id="b">B</button><span id="st"></span><span id="ac"></span>'
    "<script>aegis.ready((state) => {"
    "  st.textContent = JSON.stringify(state);"
    "  const accent = () => (ac.textContent = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim());"
    "  accent(); setInterval(accent, 100);"
    '  b.onclick = () => aegis.submit({pick: "b"}, "Picked B");'
    "});"
    "aegis.onState((s) => (st.textContent = JSON.stringify(s)));"
    "</script>"
)


def test_an_artifact_lands_when_its_page_starts_and_a_click_reaches_the_agent(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    aid, said = _artifact(page, server, PICK, 0, caption="Pick **one**")
    assert '"started": true' in said
    card = page.locator(".row.artifact .acard")
    assert card.get_attribute("data-status") == "live"
    assert "one" in page.locator(".row.artifact .cap strong").inner_text()
    frame = page.locator(f".row.artifact iframe[data-artifact={aid}]")
    assert frame.get_attribute("sandbox") == "allow-scripts"
    inner = page.frame_locator(f"iframe[data-artifact={aid}]")
    inner.locator("#st").filter(has_text="{}").wait_for()  # ready fired with the empty state
    assert inner.locator("#ac").inner_text().startswith("#")  # the theme reached the frame
    page.locator("#probes iframe").wait_for(state="detached")  # the probe frame is gone

    inner.locator("#b").click()
    page.wait_for_selector(f".row.inbox .from >> text=artifact:{aid} · submit", timeout=10000)
    page.wait_for_selector(".row.artifact .acard[data-status=submitted] .done >> text=Picked B")
    assert page.locator(".row.artifact iframe").count() == 0
    page.locator(".row.artifact button.show").click()
    page.frame_locator(".row.artifact iframe").locator("html[data-status=submitted]").wait_for()

    page.reload()
    page.wait_for_selector(".row.artifact .acard[data-status=submitted] .done >> text=Picked B")
    assert page.errors == []


def test_a_page_that_throws_fails_the_send_and_shows_no_card(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    _, said = _artifact(page, server, "<script>aegis.state({}); nope();</script>", 0)
    assert said.startswith("mcp error: page_error") and "nope is not defined" in said
    assert page.locator(".row.artifact").count() == 0
    page.locator("#probes iframe").wait_for(state="detached")
    # An error inside ready(), after the handshake, is caught by the grace window.
    _, said = _artifact(page, server, "<script>aegis.ready(() => { aegis.state({}); later(); });</script>", 2)
    assert said.startswith("mcp error: page_error") and "later is not defined" in said
    assert page.locator(".row.artifact").count() == 0
    assert page.errors == []


def test_agent_state_is_pushed_without_reloading_the_frame_and_the_theme_follows(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    aid, _ = _artifact(page, server, PICK, 0)
    inner = page.frame_locator(f"iframe[data-artifact={aid}]")
    inner.locator("#st").filter(has_text="{}").wait_for()
    page.evaluate(f"document.querySelector('iframe[data-artifact={aid}]').dataset.mark = 'same'")
    page.fill("#input", f"/mcp artifact_update {json.dumps({'id': aid, 'state': {'n': 7}})}")
    page.press("#input", "Enter")
    inner.locator("#st").filter(has_text='{"n":7}').wait_for()
    assert page.get_attribute(f"iframe[data-artifact={aid}]", "data-mark") == "same"  # not remounted
    before = inner.locator("#ac").inner_text()
    pick(page, "#theme", "logbook")
    inner.locator("#ac").filter(has_not_text=before).wait_for()
    assert page.errors == []
```

`turns_done(page, n)` (waits for `n` "done in" rows), `spawn(page)` and `pick(page, "#theme", "logbook")` already exist in `tests/test_browser.py`; read them first.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q -m browser -k "artifact"`
Expected: FAIL, no `.row.artifact`.

- [ ] **Step 3: Implement**

`src/aegis/client/js/artifacts.js`:

```js
// The page's side of an agent's artifact: one message listener for every
// frame in the transcript and every hidden probe frame. A message is taken
// only from a window this page mounted, so a page can never name another
// artifact; the operations are called with the id the frame's row carries.
// The theme reaches a frame as CSS variables, read from this page's root.

const VARS = ["--bg", "--ink", "--accent", "--accent-soft", "--on-accent", "--surface", "--raised",
  "--rule", "--muted", "--faint", "--strong", "--fill", "--ok", "--warn", "--err", "--err-bg",
  "--add-bg", "--del-bg", "--font-ui", "--font-mono", "--font-prose", "--font-chrome", "--font-head",
  "--prose-size", "--r", "--r-lg"];
const PROBE_GRACE_MS = 250; // an error right after ui/initialize (a throwing ready) still fails the probe
const PROBE_TIMEOUT_MS = 3000;
const MAX_HEIGHT = () => Math.round(window.innerHeight * 0.7);

let call = async () => {};
let entryOf = () => null;
const probes = new Map(); // iframe -> {id, log_id, timer, grace, done}

export function setup(opts) {
  call = opts.call;
  entryOf = opts.entry;
}

export function themeVars() {
  const cs = getComputedStyle(document.documentElement);
  return Object.fromEntries(VARS.map((v) => [v, cs.getPropertyValue(v).trim()]).filter(([, v]) => v));
}

const send = (frame, m) => frame.contentWindow?.postMessage(m, "*");
const notify = (frame, method, params) => send(frame, { jsonrpc: "2.0", method, params });

export function theme() {
  const vars = themeVars();
  for (const f of document.querySelectorAll("iframe[data-artifact]")) notify(f, "aegis/theme", { theme: vars });
}

// A probe request from the transcript channel: run the page hidden, report once.
export function probe(req, logId) {
  const f = document.createElement("iframe");
  f.setAttribute("sandbox", "allow-scripts");
  f.src = req.url;
  const p = { id: req.id, log_id: logId, done: false, grace: 0, timer: 0 };
  p.timer = setTimeout(() => finish(f, null), PROBE_TIMEOUT_MS); // the server has given up too
  probes.set(f, p);
  document.getElementById("probes").append(f);
}

function finish(frame, outcome) {
  const p = probes.get(frame);
  if (!p || p.done) return;
  p.done = true;
  clearTimeout(p.timer);
  clearTimeout(p.grace);
  probes.delete(frame);
  frame.remove();
  if (outcome) call("artifact.probed", { log_id: p.log_id, probe_id: p.id, ...outcome });
}

function frameOf(source) {
  for (const f of document.querySelectorAll("iframe[data-artifact], #probes iframe")) if (f.contentWindow === source) return f;
  return null;
}

window.addEventListener("message", async (ev) => {
  const m = ev.data;
  if (!m || m.jsonrpc !== "2.0") return;
  const frame = frameOf(ev.source);
  if (!frame) return;
  const p = probes.get(frame);
  if (p) {
    if (m.method === "ui/initialize") {
      send(frame, { jsonrpc: "2.0", id: m.id, result: { artifact: null, state: {}, theme: themeVars(), status: "probe" } });
      p.grace = setTimeout(() => finish(frame, { started: true }), PROBE_GRACE_MS);
    } else if (m.method === "aegis/error") {
      finish(frame, { started: false, message: m.params?.message || "error", stack: m.params?.stack || "" });
    }
    return;
  }
  const id = frame.dataset.artifact;
  const e = entryOf(id);
  const status = frame.dataset.status || e?.status || "live";
  if (m.method === "ui/initialize") {
    send(frame, { jsonrpc: "2.0", id: m.id, result: { artifact: id, state: e?.detail?.state ?? {}, theme: themeVars(), status } });
    return;
  }
  if (m.method === "aegis/size") {
    frame.style.height = `${Math.min(Math.max(80, m.params?.height || 0), MAX_HEIGHT())}px`;
    return;
  }
  const ops = { "aegis/state": "artifact.state", "aegis/emit": "artifact.emit", "aegis/submit": "artifact.submit", "aegis/error": "artifact.error" };
  const op = ops[m.method];
  if (!op) return;
  try {
    await call(op, { artifact_id: id, ...(m.params || {}) });
    if (m.id !== undefined) send(frame, { jsonrpc: "2.0", id: m.id, result: "ok" });
    if (m.method === "aegis/submit") notify(frame, "aegis/status", { status: "submitted" });
  } catch (err) {
    if (m.id !== undefined) send(frame, { jsonrpc: "2.0", id: m.id, error: { code: err.code || "error", message: err.message } });
    if (err.code === "not_live") notify(frame, "aegis/status", { status: entryOf(id)?.status || "closed" });
  }
});

// entries.js asks, through a DOM event, for a state push or a status change on a frame it kept.
document.addEventListener("aegis:state", (ev) => notify(ev.target, "aegis/state", { state: ev.detail }));
document.addEventListener("aegis:status", (ev) => notify(ev.target, "aegis/status", { status: ev.detail }));
```

`entries.js`: add the renderer and the updater.

```js
  artifact(e) {
    // An agent's interactive page: a card like a file's, the frame while
    // live, one line with the label after. Status and label were decided in
    // the fold; the frame's state is handed over by js/artifacts.js on init.
    const det = e.detail || {};
    const body = el("div", "body");
    if (e.md) {
      const cap = markdown(e.md);
      cap.classList.add("cap");
      body.append(cap);
    }
    const card = el("div", "fcard acard");
    card.dataset.status = e.status;
    const bar = el("div", "fbar");
    const acts = el("span", "acts");
    const open = el("a", "btn open", "↗ Open");
    open.href = fileBase + det.url;
    open.target = "_blank";
    open.rel = "noopener noreferrer";
    acts.append(open);
    if (e.status !== "live") acts.prepend(el("button", "btn show", "Show"));
    bar.append(el("span", "ic", e.glyph), el("span", "fn", e.title), el("span", "fs", e.summary), acts);
    card.append(bar);
    if (e.status === "live") card.append(artifactStage(e));
    else {
      const what = det.label || (e.status === "closed" ? "closed by the agent" : "answered");
      card.append(el("div", "done", `${what} · ${hhmm(det.ended_ts)}`));
    }
    body.append(card);
    return row(e, "artifact", body);
  },
```

```js
// The frame of an artifact's card, live or shown again read-only.
export function artifactStage(e) {
  const stage = el("div", "stage html");
  const f = el("iframe");
  f.setAttribute("sandbox", "allow-scripts");
  f.loading = "lazy";
  f.src = fileBase + e.detail.url;
  f.title = e.title;
  f.dataset.artifact = e.id;
  f.dataset.status = e.status;
  stage.append(f);
  return stage;
}

// A row updated in place, so a live frame is not reloaded by every patch.
// True when the node now shows the entry; false when it must be remounted.
const UPDATERS = {
  artifact(e, node) {
    const card = node.querySelector(".acard");
    const frame = node.querySelector("iframe[data-artifact]");
    if (!card || card.dataset.status !== e.status || !frame) return false;
    if ((node.querySelector(".cap")?.textContent || "") !== markdown(e.md || "").textContent) return false;
    if (e.detail?.state_by === "agent") frame.dispatchEvent(new CustomEvent("aegis:state", { detail: e.detail.state, bubbles: true }));
    return true;
  },
};
export function update(e, node) {
  const u = UPDATERS[e.kind];
  return u ? u(e, node) : false;
}
```

`transcript.js` `apply`, the `if (old)` branch:

```js
        if (old) {
          if (update(e, old)) this.nodes.set(e.id, old);
          else {
            this.watch.unobserve(old);
            old.replaceWith(this.mount(e));
          }
        }
```

with `import { render, update } from "./entries.js";`.

`app.js`:
- `import * as artifacts from "./artifacts.js";` and `import { artifactStage } from "./entries.js";`
- after `callFor` is defined: `artifacts.setup({ call: (op, params) => callFor(shown, op, params), entry: (id) => transcript.entries.get(id) });` (place it after `transcript` is constructed);
- in the theme `change` listener: `artifacts.theme();`
- in the transcript subscription's patch handler: `(ops) => { for (const op of ops) if (op.probe) artifacts.probe(op.probe, where.log_id); transcript.apply(ops); }`
- a click handler next to the Open-natively one:

```js
// Show on a finished artifact's card: the frame again, read-only.
$("entries").addEventListener("click", (ev) => {
  const b = ev.target.closest(".acard .show");
  if (!b) return;
  const rowEl = b.closest(".row");
  const e = transcript.entries.get(rowEl.dataset.id);
  const card = rowEl.querySelector(".acard");
  const old = card.querySelector(".stage");
  if (old) old.remove();
  else card.append(artifactStage(e));
});
```

`index.html`: `<div id="probes" hidden></div>` just before the module script. `base.css`, after the `.fcard` rules: `#a2 .acard .stage iframe{height:240px;background:var(--bg)}`, `#a2 .acard .done{padding:10px 12px;color:var(--muted);font-family:var(--font-chrome);font-size:12.5px;border-top:1px solid var(--rule)}`, `#a2 .acard[data-status=submitted] .fbar .ic{color:var(--ok)}`, `#probes{position:fixed;width:1px;height:1px;overflow:hidden;opacity:0;pointer-events:none}` (a `hidden` iframe does not load in every browser; keep the div hidden from readers with `aria-hidden` and the CSS above, and drop the `hidden` attribute if Chromium refuses to load the probe).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q -m browser -k "artifact or sent_file or long_transcript"` then `uv run pytest -q tests/test_client_rules.py`
Expected: all pass. Then start `uv run aegis serve` in a scratch root with the fake claude, land one artifact from the composer with `/mcp artifact_create …` and look at the card in all three themes.

- [ ] **Step 5: Commit**

```bash
git commit -F - -- src/aegis/client/js/artifacts.js src/aegis/client/js/entries.js src/aegis/client/js/transcript.js src/aegis/client/js/app.js src/aegis/client/index.html src/aegis/client/css/base.css tests/test_browser.py <<'EOF'
feat(client): artifact cards: the frame, the bridge, hidden probes, updates in place (#217)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
```

### Task 7: telling the agent, and the docs

**Files:**
- Modify: `src/aegis/mcp.py` (`PRIMER`), `DESIGN.md` (security section, after the sent-file paragraph), `docs/superpowers/specs/2026-10-09-aegis-artifacts-design.md` (status), `tests/test_live.py`
- Create: `changelog.d/217-artifacts.added.md`

- [ ] **Step 1: The primer paragraph**, inserted after the `file_send` paragraph in `PRIMER`:

```
When the person has to choose among things that must be seen, tune values, \
answer a question whose answer has structure, or play with an explanation, \
show them a page instead of asking in prose: artifact_create writes a working \
skeleton and returns its path; edit it with your Edit tool; artifact_send lands \
it in the transcript after running it hidden in their browser, and refuses a \
page that throws, so edit and send again until it lands. Only then write your \
message, which may refer to the card above it, and end the turn with \
turn_end(needs_you) when the page asks something. The caption is the one line \
they read before the card; a lesson whose text belongs beside its controls \
carries it inside the page. Their answer reaches you as a user turn headed \
`> from artifact:<id> · submit|<event>|error · …` with the JSON in a code \
block; artifact_read gives you the page's state at any time without waking \
anyone.
```

Check `uv run pytest -q tests/test_harness.py -k primer` still passes (it checks the tool prefix in the primer).

- [ ] **Step 2: The live test**, appended to `tests/test_live.py`, in the shape of `test_real_claude_sends_a_file_and_the_link_serves_it`:

```python
async def test_real_claude_lands_an_artifact_and_hears_the_pick(tmp_path: Path):
    """The real binary finds artifact_create and artifact_send from their
    descriptions, lands a page with two choices, and answers the pick the
    person makes through the page."""
    import asyncio

    import uvicorn

    from aegis.app import App
    from aegis.roots import make_roots
    from aegis.web import build_web

    from .test_agents import _free_port

    claude = shutil.which("claude")
    assert claude, "claude is not on PATH"
    (tmp_path / ".aegis.yaml").write_text(
        f"agents:\n  haiku: {{harness: claude-code, model: {HAIKU}, effort: low, permission: full}}\n"
    )
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    app = App(make_roots(tmp_path, None), claude_bin=claude, base_url=base)
    server = uvicorn.Server(
        uvicorn.Config(
            build_web(app, "t", {f"127.0.0.1:{port}"}), port=port, log_level="warning"
        )
    )
    task = asyncio.create_task(server.serve())
    await until(lambda: server.started, timeout=10, what="uvicorn")
    try:
        r = await app.registry.call("session.spawn", {"agent": "haiku"})
        s = app.sessions.sessions[r["log_id"]]
        await s.send(
            "Offer me two layouts, A and B, as an aegis artifact with one button "
            "each, captioned 'Pick a layout'. Then end your turn and wait for my pick."
        )
        await until(
            lambda: s.status == "idle"
            and any(e["kind"] == "artifact" for e in s.entries()),
            timeout=180,
            what="the artifact",
        )
        (art,) = [e for e in s.entries() if e["kind"] == "artifact"]
        assert art["status"] == "live"
        await app.registry.call(
            "artifact.submit",
            {"log_id": s.log_id, "artifact_id": art["id"], "data": {"layout": "B"}, "label": "Picked B"},
        )
        await until(
            lambda: s.status == "idle"
            and "B" in ([e["md"] for e in s.entries() if e["kind"] == "prose"] or [""])[-1],
            timeout=120,
            what="the answer",
        )
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 30)
```

Run: `uv run pytest -q --run-live -m live -k artifact` (spends quota; run once).

- [ ] **Step 3: DESIGN.md**, one paragraph after "A sent file's URL is its key…":

```
**An artifact is a sent page with a way back, and the way back is the bridge.**
An agent's interactive page (`artifacts.py`, `artifact_ops.py`) is served and
framed exactly like a sent HTML file, so its script runs in an opaque origin
that the websocket refuses. Its only path to the server is `postMessage` to
the host page, whose bridge (`client/js/artifacts.js`) answers only windows it
mounted and calls four person operations with the artifact id the frame's row
carries, never one the page sent. Every payload is capped and emits are
rate-limited on the server. A page's answer reaches the agent through the
inbox like a monitor's wake, so a click mid-turn is held. A page lands only
after the static checks and a hidden run in a browser (`Board.probe`): a page
that throws is a tool error, not a card.
```

- [ ] **Step 4: The changelog fragment** `changelog.d/217-artifacts.added.md`:

```markdown
- **Agents hand the person an interactive page and hear the answer.**
  `artifact_create` writes a working skeleton, the agent edits it,
  `artifact_send` runs it hidden in the browser and lands it in the transcript
  only when it starts. A click, a submit or a script error reaches the agent
  as an inbox turn; state the page keeps is read with `artifact_read`; the
  agent pushes new state or a new page with `artifact_update`. Until now an
  agent could only send a static file and ask in prose.
```

- [ ] **Step 5: Spec status** line → `**Status: implemented, <today's date>** (issue #217), following this plan.` Then `make changelog-check`, `rift check`, and commit:

```bash
git commit -F - -- src/aegis/mcp.py DESIGN.md changelog.d/217-artifacts.added.md docs/superpowers/specs/2026-10-09-aegis-artifacts-design.md tests/test_live.py <<'EOF'
docs(artifacts): the primer paragraph, the design rule, the release note and the live test (#217)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
```

### Task 8: land it

- [ ] `make check` (format, lint, lint-docs, changelog-check, test, typecheck) in the worktree; `make format-check` before pushing (CI fails on format, `make check` rewrites silently).
- [ ] `make test-browser` whole, not only `-k artifact`: the `transcript.apply` change touches every row.
- [ ] `make bench`; paste the table in the PR body.
- [ ] A real `aegis serve` from the branch with the fake claude: create, break, fix and land one artifact from the composer; submit it; reload; switch themes. Screenshot the live card and the collapsed card.
- [ ] Mark this plan's status done, push, open the PR closing #217 with: what was measured (bench, the browser run), what was tried and dropped (the probe grace window's value if it moved, the `hidden` attribute if Chromium refused to load the probe frame), and what was left out (slice 2).
- [ ] Stop at the PR: Alex merges.
