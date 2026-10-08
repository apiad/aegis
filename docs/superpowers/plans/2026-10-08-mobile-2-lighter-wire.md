# Mobile slice 2: the lighter wire, implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A transcript snapshot carries no collapsed detail, and a return to a tab or a reconnect receives only what changed since the client last saw it.

**Architecture:** The fold stamps every entry with `rev`, the store index of the record that last changed it, and logs removals with theirs. `Fold.snapshot(since)` answers with everything, or with the entries changed after `since`, the ids removed after it, and every live (streaming) entry. Snapshots and patches go through one projection, `transcript/wire.py`, that drops what a closed `<details>` shows; `transcript.detail` returns it when a row opens. The `sub` message gains an optional `since`, the channel resolver receives it, and the client keeps the last 8 tabs it visited and subscribes with the `rev` it holds.

**Tech Stack:** Python 3.13, Starlette websocket, pydantic; plain ES modules; pytest, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-08-mobile-and-pwa-design.md`, section 1, "The lighter wire".

## Global Constraints

- Python decides, the browser draws: what is lazy is decided in `transcript/wire.py`, never in JS.
- Imports inside `aegis` are relative; nothing imports `legacy/` (`tests/test_imports.py`).
- The client is plain ES modules, no build step, no framework.
- The protocol version goes from 1 to 2 (`PROTO` in `src/aegis/web.py` and `src/aegis/client/js/protocol.js`), because the transcript snapshot's `data` changes from a list to an object.
- Every tool row starts closed, failures included; the `collapsed` key leaves tool entries.
- The client keeps at most 8 tabs (`TAB_CACHE = 8`).
- `transcript.detail` takes at most 100 ids.
- Release notes are fragments in `changelog.d/`; categories are `added`, `changed`, `deprecated`, `removed`, `fixed`, `performance`, `security`.
- `make bench` runs and its table goes in the PR body.

## Review Focus

- A client that left while OpenCode was streaming a part and comes back while it still streams: the live entry has no new store record, so only the "live entries are in every delta" rule brings its new text. Pinned by Task 1's mid-stream replay (it fails 4,344 of 15,977 comparisons without the rule).
- A `since` larger than the store's last index (a client that met a different store under the same log id, or a hand-made message): must get a full snapshot, never an empty delta. Pinned in Task 1.
- An open row whose tool result lands while it is open: the upsert has a newer `rev`, the cached detail is stale, and the row must refetch rather than show the old tail. Pinned in Task 4's browser test.
- Switching tabs while a `transcript.detail` call is in flight: the answer belongs to the previous tab and must not be merged into the new one. Pinned in Task 4 (the loader drops answers for a tab no longer shown).
- A server restart between two visits to a tab: revisions come from the store, so the cursor stays valid and the delta is small. Covered by the existing `test_a_restart_brings_tabs_back_stopped_and_a_prompt_resumes`, which must still pass.

---

### Task 1: Revisions, removals and `Fold.snapshot`

**Files:**
- Modify: `src/aegis/transcript/entries.py` (class `Fold`: `__init__`, `apply`, `_upsert`, `_remove`; new `snapshot`, `entry`; the tool `detail.update(...)` near line 606)
- Modify: `tests/test_fold.py:152` and `tests/test_fold.py:203-212`
- Create: `tests/test_wire.py`

**Interfaces:**
- Produces: every entry dict gains `"rev": int`. `Fold.snapshot(since: int | None = None) -> dict` returning `{"rev": int, "entries": list[dict]}` for a full snapshot, or `{"rev": int, "since": int, "removed": list[str], "entries": list[dict]}` for a delta; entries are passed through `wire()` from Task 2 (in this task, pass them through unchanged; Task 2 wraps them). `Fold.entry(id: str) -> dict | None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_wire.py`:

```python
"""What a transcript looks like on the wire: revisions, deltas and the lazy
detail (DESIGN.md, "The patches add up to the entries")."""

import json
from pathlib import Path

import pytest

from aegis.opencode.stream import DELTA
from aegis.transcript.entries import Fold, fold_records
from aegis.transcript.store import read_store

from .test_fold import Rec, run
from .test_opencode_stream import first_prompt, lines

FIX = Path(__file__).parent / "fixtures"


def rebuild(held: list[dict], snap: dict) -> list[dict]:
    """What a client holding ``held`` has after applying ``snap``: removals
    first, then upserts. A dict, like the client's Map, keeps a known id in
    place and appends a new one."""
    shown = {e["id"]: e for e in held}
    for id in snap.get("removed", []):
        shown.pop(id, None)
    for e in snap["entries"]:
        shown[e["id"]] = e
    return list(shown.values())


def test_every_entry_carries_the_index_of_the_record_that_last_changed_it():
    r = Rec()
    r.own("send", text="hello")  # 0: a pending user entry
    r.echo("hello")  # 1: the echo replaces it
    r.call("t1", "Bash", {"command": "ls"})  # 2
    r.output("t1", "a\nb")  # 3: the call's entry changes again
    f, _ = run(r)
    assert {e["id"]: e["rev"] for e in f.entries()} == {"e1.0": 1, "t1": 3}


@pytest.mark.parametrize("name", ["session.jsonl", "slash-commands.jsonl"])
def test_a_delta_from_any_cut_rebuilds_the_entries(name):
    records, _ = read_store(FIX / name)
    final = fold_records(records)
    want = final.snapshot()["entries"]
    for k in range(len(records) + 1):
        held = fold_records(records[:k]).snapshot()
        assert rebuild(held["entries"], final.snapshot(held["rev"])) == want, k


def stream(name: str):
    """Replay an OpenCode fixture the way a session does (deltas through
    ``live``, the rest as records) and yield the fold after every step."""
    f, i = Fold(), 0

    def own(kind, **kw):
        nonlocal i
        f.apply({"i": i, "ts": 1.0 + i, "src": "aegis", "kind": kind, **kw})
        i += 1

    own("spawn", agent="deep", model="m", cwd="/x")
    own("send", text=first_prompt(name))
    for ln in lines(name):
        evs = f.parse("opencode", ln)
        if json.loads(ln)["type"] == DELTA:
            f.live(evs)
        else:
            f.apply({"i": i, "ts": 1.0 + i, "src": "opencode", "line": ln}, evs)
            i += 1
        yield f


@pytest.mark.parametrize("name", ["plain", "tool", "edit", "task", "abort"])
def test_a_delta_between_two_moments_of_a_stream_rebuilds_the_later_one(name):
    """A client that left mid-stream and returns while the part still streams:
    a live entry grows without a store record, so only the rule that every
    delta carries the live entries brings its new text."""
    seen: list[tuple[list[dict], int]] = []
    for f in stream(name):
        now = f.snapshot()
        copy = json.loads(json.dumps(now["entries"]))
        for held, rev in seen[-40:]:
            assert rebuild(held, f.snapshot(rev)) == copy
        seen.append((copy, now["rev"]))


def test_a_since_past_the_store_gets_a_full_snapshot():
    r = Rec()
    r.own("send", text="hello")
    r.echo("hello")
    f, _ = run(r)
    for since in (5, 99, -2):
        snap = f.snapshot(since)
        assert "since" not in snap and snap["entries"] == f.snapshot()["entries"]


def test_an_empty_fold_has_rev_minus_one_and_a_delta_from_it_is_everything():
    f = Fold()
    assert f.snapshot() == {"rev": -1, "entries": []}
    r = Rec()
    r.own("send", text="hello")
    g, _ = run(r)
    assert rebuild([], g.snapshot(-1)) == g.snapshot()["entries"]


def test_entry_finds_one_entry_by_id():
    r = Rec()
    r.call("t1", "Bash", {"command": "ls"})
    f, _ = run(r)
    assert f.entry("t1")["kind"] == "tool"
    assert f.entry("nope") is None
```

In `tests/test_fold.py`, delete line 152 (`assert done["detail"]["collapsed"] is True`) and replace the test at lines 203-212 with:

```python
def test_a_failure_starts_closed_like_every_tool_row():
    r = Rec()
    r.call("t1", "Bash", {"command": "mmdc"})
    r.output("t1", "Exit code 1\nError: Parse error on line 9", is_error=True)
    f, _ = run(r)
    (e,) = f.entries()
    assert e["status"] == "err" and "collapsed" not in e["detail"]
    assert e["detail"]["result"] == "Exit code 1 · Error: Parse error on line 9"
    assert "Parse error" in e["detail"]["tail"]
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest -q tests/test_wire.py tests/test_fold.py`
Expected: FAIL with `KeyError: 'rev'` and `AttributeError: 'Fold' object has no attribute 'snapshot'`, and the failure test fails on `collapsed`.

- [ ] **Step 3: Implement**

In `src/aegis/transcript/entries.py`, in `Fold.__init__` after `self._live: set[str] = set()`:

```python
        # The store index of the record being folded; stamped on every entry
        # it changes as ``rev`` (-1 before the first record).
        self._rev = -1
        # Removed entry ids, by the rev that removed them. Kept after an id
        # comes back, so a delta removes it first and appends it again, as a
        # fresh fold orders it.
        self._removed: dict[str, int] = {}
```

At the top of `Fold.apply`, change `i, ts = record["i"], record.get("ts")` to:

```python
        i, ts = record["i"], record.get("ts")
        self._rev = i
```

Replace `_upsert` and `_remove`:

```python
    def _upsert(self, e: dict) -> list[dict]:
        e["rev"] = self._rev
        self._entries[e["id"]] = e
        return [{"upsert": e}]

    def _remove(self, id: str) -> list[dict]:
        self._entries.pop(id, None)
        self._removed[id] = self._rev
        return [{"remove": id}]
```

After `def entries(self)`, add:

```python
    def entry(self, id: str) -> dict | None:
        return self._entries.get(id)

    def snapshot(self, since: int | None = None) -> dict:
        """What a subscriber gets: every entry, or, given the ``rev`` it holds,
        the entries changed after it and the ids removed after it. Live
        entries are in every delta: deltas grow them without a store record,
        so their ``rev`` does not move. A ``since`` this fold never reached
        gets everything."""
        entries = self.entries()
        if since is None or not -1 <= since <= self._rev:
            return {"rev": self._rev, "entries": entries}
        return {
            "rev": self._rev,
            "since": since,
            "removed": [i for i, r in self._removed.items() if r > since],
            "entries": [
                e for e in entries if e["rev"] > since or e["id"] in self._live
            ],
        }
```

In the `ToolOutput` branch (around line 604), change

```python
            detail.update(
                result=d.result_digest(name, ev.text, ev.is_error, pair),
                tail=d.output_tail(ev.text),
                collapsed=not ev.is_error,
            )
```

to

```python
            detail.update(
                result=d.result_digest(name, ev.text, ev.is_error, pair),
                tail=d.output_tail(ev.text),
            )
```

- [ ] **Step 4: Run them again**

Run: `uv run pytest -q tests/test_wire.py tests/test_fold.py tests/test_opencode_stream.py tests/test_session.py tests/test_session_opencode.py`
Expected: PASS. The session tests' `refold_matches` holds because `rev` comes from the store.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/transcript/entries.py tests/test_wire.py tests/test_fold.py
git commit -m "feat(transcript): entries carry their rev, and a fold answers a delta since one"
```

### Task 2: The wire projection

**Files:**
- Create: `src/aegis/transcript/wire.py`
- Modify: `src/aegis/transcript/entries.py` (`Fold.snapshot` wraps entries in `wire`)
- Modify: `src/aegis/session.py:520` and `src/aegis/session.py:568` (publish `wire_ops(ops)`)
- Modify: `tests/test_session.py:56-67`, `tests/test_session_opencode.py:77-88` (`patches_rebuild_entries` compares projected entries)
- Test: `tests/test_wire.py` (append)

**Interfaces:**
- Consumes: `Fold.snapshot` from Task 1.
- Produces: `wire(e: dict) -> dict` and `wire_ops(ops: list[dict]) -> list[dict]` in `aegis.transcript.wire`; `LAZY: dict[str, tuple[str, ...]]`. A projected entry with lazy fields dropped has `detail["more"] is True`; a thinking entry with text has `md: None` and `detail["more"] is True`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_wire.py`:

```python
from aegis.transcript.wire import LAZY, wire, wire_ops


def test_wire_drops_what_a_closed_row_shows_and_says_there_is_more():
    r = Rec()
    r.call("t1", "Edit", {"file_path": "a.py", "old_string": "x = 1", "new_string": "x = 2"})
    r.output("t1", "The file a.py has been updated.")
    r.call("t2", "Bash", {"command": "ls"})
    r.output("t2", "a\nb")
    f, _ = run(r)
    for e in f.entries():
        w = wire(e)
        assert w["detail"]["more"] is True
        assert not set(LAZY["tool"]) & set(w["detail"])
        assert w["detail"]["result"] == e["detail"]["result"]
        assert w["summary"] == e["summary"] and w["rev"] == e["rev"]
        assert "args" in e["detail"], "the fold itself keeps everything"


def test_wire_leaves_prose_errors_and_files_whole():
    for e in (
        {"id": "p", "kind": "prose", "md": "hi", "detail": {}},
        {"id": "x", "kind": "error", "md": None, "detail": {"tail": "stderr"}},
        {"id": "f", "kind": "file", "md": None, "detail": {"url": "/files/a/b"}},
    ):
        assert wire(e) is e


def test_wire_drops_thinking_text_but_not_an_empty_thought():
    e = {"id": "t", "kind": "thinking", "md": "deep thoughts", "detail": {}}
    assert wire(e)["md"] is None and wire(e)["detail"]["more"] is True
    empty = {"id": "u", "kind": "thinking", "md": "", "detail": {}}
    assert wire(empty) is empty


def test_wire_ops_project_upserts_and_pass_removals():
    e = {"id": "t", "kind": "thinking", "md": "deep", "detail": {}}
    assert wire_ops([{"upsert": e}, {"remove": "z"}]) == [
        {"upsert": wire(e)},
        {"remove": "z"},
    ]


def test_snapshots_carry_projected_entries():
    r = Rec()
    r.call("t1", "Bash", {"command": "ls"})
    r.output("t1", "a\nb")
    f, _ = run(r)
    (e,) = f.snapshot()["entries"]
    assert "tail" not in e["detail"] and e["detail"]["more"] is True
    (d,) = f.snapshot(-1)["entries"]
    assert "tail" not in d["detail"]
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest -q tests/test_wire.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'aegis.transcript.wire'`.

- [ ] **Step 3: Implement**

Create `src/aegis/transcript/wire.py`:

```python
"""What a transcript entry looks like on the wire.

The fold keeps every entry whole. A browser gets each one without what a closed
``<details>`` shows: a tool's arguments, output tail and diff, a system note's
tail, a typed command's template, and thinking text. A row fetches them with
``transcript.detail`` when it opens. Over 80 real transcripts those fields were
75% of a snapshot's bytes, and nobody reads them until they open the row.

``more`` tells the browser a row has something to fetch. Python decides what is
lazy; the browser only asks for it (DESIGN.md, "Python decides, the browser
draws").
"""

from __future__ import annotations

# Per entry kind, the detail fields a closed row hides.
LAZY: dict[str, tuple[str, ...]] = {
    "tool": ("args", "tail", "diff"),
    "system": ("tail",),
    "user": ("tail",),
}


def wire(e: dict) -> dict:
    """``e`` as a browser receives it; ``e`` itself when nothing is lazy."""
    if e["kind"] == "thinking":
        if not e.get("md"):
            return e
        return {**e, "md": None, "detail": {**e["detail"], "more": True}}
    lazy = [k for k in LAZY.get(e["kind"], ()) if e["detail"].get(k)]
    if not lazy:
        return e
    detail = {k: v for k, v in e["detail"].items() if k not in lazy}
    return {**e, "detail": {**detail, "more": True}}


def wire_ops(ops: list[dict]) -> list[dict]:
    """Patch ops as a browser receives them."""
    return [{"upsert": wire(op["upsert"])} if "upsert" in op else op for op in ops]
```

In `src/aegis/transcript/entries.py`, add `from .wire import wire` next to `from . import describe as d`, and in `Fold.snapshot` wrap both entry lists: `"entries": [wire(e) for e in entries]` in the full branch, and `wire(e) for e in entries if ...` in the delta branch.

In `src/aegis/session.py`, add `from .transcript.wire import wire_ops` next to the existing `from .transcript.entries import ...` import, then change line 520 `self._publish(self.channel, ops)` to `self._publish(self.channel, wire_ops(ops))`, and line 568 `self._publish(self.channel, fold.live(events))` to `self._publish(self.channel, wire_ops(fold.live(events)))`. `_record` keeps using the unprojected `ops` for `activity`.

In `tests/test_session.py`, add `from aegis.transcript.wire import wire` and change the last line of `patches_rebuild_entries` to:

```python
        return list(shown.values()) == [wire(e) for e in self.session.entries()]
```

Make the same two changes in `tests/test_session_opencode.py` (`patches_rebuild_entries`, line 88).

- [ ] **Step 4: Run them again**

Run: `uv run pytest -q tests/test_wire.py tests/test_session.py tests/test_session_opencode.py tests/test_fold.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/transcript/wire.py src/aegis/transcript/entries.py src/aegis/session.py tests/test_wire.py tests/test_session.py tests/test_session_opencode.py
git commit -m "feat(transcript): the wire carries no collapsed detail"
```

### Task 3: `since` on subscribe, `transcript.detail`, protocol 2

**Files:**
- Modify: `src/aegis/channels.py` (`Channels.__init__` resolver type, `subscribe`, module docstring)
- Modify: `src/aegis/app.py` (`_resolve`, `_register`: new `DetailParams` and `transcript.detail`)
- Modify: `src/aegis/registry.py:251` (`transcript(log_id, since)`; new `detail(log_id, ids)`)
- Modify: `src/aegis/web.py` (`PROTO = 2`; the `sub` branch passes `since`)
- Modify: `tests/test_channels.py` (`make()`), `tests/test_web.py` (`Conn.hello`, snapshot shape)
- Test: `tests/test_channels.py`, `tests/test_web.py` (append)

**Interfaces:**
- Consumes: `Fold.snapshot(since)` and `Fold.entry(id)`.
- Produces: `Channels(resolve: Callable[[str, int | None], Snapshot | None])`; `Channels.subscribe(channel: str, send: Send, since: int | None = None) -> Sub`; `Registry.transcript(log_id: str, since: int | None = None)`; `Registry.detail(log_id: str, ids: list[str]) -> list[dict]`; operation `transcript.detail` with params `{"log_id": str, "ids": list[str]}` (1 to 100 ids) returning the full entries found, in the order asked. Websocket `sub` message: `{"t": "sub", "channel": str, "since"?: int}`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_channels.py`, change `make()` to pass `since` through:

```python
def make():
    state = {"n": 0}
    return Channels(
        lambda name, since=None: (lambda: {**state, "since": since}) if name == "c" else None
    ), state
```

and append:

```python
def test_since_reaches_the_channel_resolver():
    ch, _ = make()
    got = []
    ch.subscribe("c", got.append, since=7)
    ch.subscribe("c", got.append)
    assert [m["data"]["since"] for m in got] == [7, None]
```

In `tests/test_web.py`, add `PROTO` to the import (`from aegis.web import PROTO, load_or_create_token, build_web`), change `Conn.hello` to send and expect `PROTO`:

```python
    def hello(self):
        self.ws.send_json({"t": "hello", "token": TOKEN, "proto": PROTO})
        msg = self.ws.receive_json()
        assert msg["t"] == "welcome" and msg["proto"] == PROTO
        return self
```

change `snap["data"][0]["summary"]` to `snap["data"]["entries"][0]["summary"]` (two places, in `test_spawn_send_and_watch_a_turn` and the spawn-overrides test), and `has_prompt(snap["data"])` to `has_prompt(snap["data"]["entries"])`. Then append:

```python
def test_a_resubscribe_with_since_gets_only_what_changed(project, fake_claude):
    with (
        client_for(project, fake_claude) as c,
        c.websocket_connect("/ws", headers=ORIGIN) as ws,
    ):
        conn = Conn(ws).hello()
        log_id = conn.call("session.spawn", agent="opus")["result"]["log_id"]
        ch = f"transcript:{log_id}"

        def snapshot(**extra):
            ws.send_json({"t": "sub", "channel": ch, **extra})
            return conn.until(lambda m: m["t"] == "snapshot" and m["channel"] == ch)[
                "data"
            ]

        def turn(text):
            conn.call("session.send", log_id=log_id, text=text)
            conn.until(
                lambda m: m["t"] == "patch"
                and m["channel"] == ch
                and any(
                    op.get("upsert", {}).get("summary", "").startswith("done in")
                    for op in m["ops"]
                )
            )

        snapshot()  # subscribed, so the turns' patches reach us
        turn("first")
        full = snapshot()
        assert "since" not in full and full["rev"] >= 0
        turn("second")
        delta = snapshot(since=full["rev"])
        assert delta["since"] == full["rev"] and delta["rev"] > full["rev"]
        assert delta["entries"] and all(e["rev"] > full["rev"] for e in delta["entries"])
        assert len(delta["entries"]) < len(full["entries"]) + 4
        assert "since" not in snapshot(since=full["rev"] + 10_000)
        conn.call("session.close", log_id=log_id)


def test_transcript_detail_returns_what_the_wire_left_out(project, fake_claude):
    with (
        client_for(project, fake_claude) as c,
        c.websocket_connect("/ws", headers=ORIGIN) as ws,
    ):
        conn = Conn(ws).hello()
        log_id = conn.call("session.spawn", agent="opus")["result"]["log_id"]
        ch = f"transcript:{log_id}"
        ws.send_json({"t": "sub", "channel": ch})
        conn.until(lambda m: m["t"] == "snapshot" and m["channel"] == ch)
        conn.call("session.send", log_id=log_id, text="/bash list it => SECRET-OUT")
        patch = conn.until(
            lambda m: m["t"] == "patch"
            and m["channel"] == ch
            and any(op.get("upsert", {}).get("kind") == "tool" and op["upsert"]["status"] == "ok" for op in m["ops"])
        )
        tool = next(op["upsert"] for op in patch["ops"] if op.get("upsert", {}).get("kind") == "tool")
        # The closed row keeps its one-line result; the tail stays behind.
        assert "tail" not in tool["detail"] and tool["detail"]["more"] is True
        (full,) = conn.call("transcript.detail", log_id=log_id, ids=[tool["id"]])["result"]
        assert "SECRET-OUT" in full["detail"]["tail"] and full["rev"] == tool["rev"]
        assert conn.call("transcript.detail", log_id=log_id, ids=["nope"])["result"] == []
        assert conn.call("transcript.detail", log_id="nope", ids=["x"])["error"]["code"] == "no_session"
        assert conn.call("transcript.detail", log_id=log_id, ids=[])["error"]["code"] == "bad_params"
        conn.call("session.close", log_id=log_id)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest -q tests/test_channels.py tests/test_web.py`
Expected: FAIL: `subscribe() got an unexpected keyword argument 'since'`, the welcome's `proto` is 1, and `unknown_op` for `transcript.detail`.

- [ ] **Step 3: Implement**

`src/aegis/channels.py`: change the docstring's second paragraph to

```python
"""Named state channels: a snapshot on subscribe, then numbered patches.

Each subscription numbers its own patches from 1, after the snapshot's 0. A
client that sees a gap, or reconnects, resubscribes and takes a fresh snapshot;
that one rule covers dropped frames, a sleeping laptop and a server restart. A
subscribe may carry ``since``, the revision the client already holds; a channel
that keeps revisions (a transcript) answers with only what changed after it,
and any other channel ignores it.
Adding a subsystem adds channels, never a protocol field.
"""
```

and

```python
Resolve = Callable[[str, int | None], Snapshot | None]


class Channels:
    def __init__(self, resolve: Resolve) -> None:
        self._resolve = resolve
        self._subs: dict[str, list[Sub]] = {}

    def subscribe(self, channel: str, send: Send, since: int | None = None) -> Sub:
        snapshot = self._resolve(channel, since)
```

(the rest of `subscribe` is unchanged).

`src/aegis/registry.py`, replace `transcript` and add `detail` after it:

```python
    def transcript(self, log_id: str, since: int | None = None):
        """A snapshot function for any session's transcript, archived included."""
        s = self.sessions.get(log_id)
        if s is not None:
            return lambda: s.fold().snapshot(since)
        if log_id in self.archived:
            return lambda: self._archived_fold(log_id).snapshot(since)
        return None

    def detail(self, log_id: str, ids: list[str]) -> list[dict]:
        """The whole entries ``ids`` name, for rows the wire sent without
        their detail (transcript/wire.py); unknown ids are left out."""
        s = self.sessions.get(log_id)
        if s is not None:
            fold = s.fold()
        elif log_id in self.archived:
            fold = self._archived_fold(log_id)
        else:
            raise OpError("no_session", f"no session {log_id!r}")
        return [e for e in map(fold.entry, ids) if e is not None]

    def _archived_fold(self, log_id: str) -> Fold:
        path = self.store_path(log_id)
        return fold_records(read_store(path)[0]) if path.exists() else Fold()
```

and add `Fold` to the existing `from .transcript.entries import ...` line in `registry.py` (check that `OpError` is already imported there; it is used by `open`).

`src/aegis/app.py`: change `def _resolve(self, name: str):` to `def _resolve(self, name: str, since: int | None = None):` and its transcript branch to `return self.sessions.transcript(name.removeprefix("transcript:"), since)`. Add a params model after `LogParams`:

```python
class DetailParams(_Strict):
    log_id: str
    ids: list[str] = Field(min_length=1, max_length=100)
```

and register, next to `session.interrupt` in `_register`:

```python
        @r.op("transcript.detail", DetailParams)
        async def detail(p: DetailParams, caller):
            """The whole entries for rows the wire sent without their detail."""
            return reg.detail(p.log_id, p.ids)
```

`src/aegis/web.py`: `PROTO = 2`, and in the `sub` branch:

```python
                elif t == "sub":
                    ch = str(msg.get("channel"))
                    since = msg.get("since")
                    if not isinstance(since, int) or isinstance(since, bool):
                        since = None
                    if ch in subs:
                        app.channels.unsubscribe(subs.pop(ch))
                    try:
                        subs[ch] = app.channels.subscribe(ch, out.put_nowait, since)
```

- [ ] **Step 4: Run them again**

Run: `uv run pytest -q tests/test_channels.py tests/test_web.py tests/test_window.py tests/test_registry.py`
Expected: PASS. `test_window.py` speaks `PROTO` through `window.serves`, which imports it from `web`.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/channels.py src/aegis/app.py src/aegis/registry.py src/aegis/web.py tests/test_channels.py tests/test_web.py
git commit -m "feat(web): subscribe since a revision, and fetch a row's detail; protocol 2"
```

### Task 4: The client keeps tabs, resumes, and loads detail on open

**Files:**
- Modify: `src/aegis/client/js/protocol.js` (`PROTO`, `subscribe`, `_sendSub`)
- Modify: `src/aegis/client/js/transcript.js` (constructor, `mount`, `snapshot`, `apply`, `clear`; new `resume`, `stash`, `restore`, `fetch`)
- Modify: `src/aegis/client/js/entries.js` (`tool`, `thinking`, `system`, `user` renderers)
- Modify: `src/aegis/client/js/app.js` (the `Transcript` construction and `follow`)
- Modify: `tests/test_browser.py` (`test_a_session_from_spawn_to_close`)
- Test: `tests/test_browser.py` (append)

**Interfaces:**
- Consumes: the `since` sub field, `transcript.detail`, the snapshot shape `{rev, entries, since?, removed?}`, `detail.more`.
- Produces: `Connection.subscribe(channel, onSnapshot, onPatch, onError, since)` where `since` is an optional `() => number | null`. `new Transcript(scroller, list, jump, loadDetail)` with `loadDetail(ids: string[]) => Promise<entry[]>`; `Transcript.rev`, `snapshot(data)`, `resume(data)`, `stash() -> object`, `restore(state)`.

- [ ] **Step 1: Write the failing browser tests**

In `tests/test_browser.py`, in `test_a_session_from_spawn_to_close`, replace

```python
    page.fill("#input", "/fail")
    page.press("#input", "Enter")
    turns_done(page, 2)
    assert page.is_visible(".row.tool.err pre.out")
```

with

```python
    page.fill("#input", "/fail")
    page.press("#input", "Enter")
    turns_done(page, 2)
    assert page.is_visible(".row.tool.err") and not page.is_visible(".row.tool.err pre.out")
    page.click(".row.tool.err summary")
    page.wait_for_selector(".row.tool.err pre.out", state="visible")
```

Append:

```python
def frames_on(pg) -> list[str]:
    got: list[str] = []
    pg.on("websocket", lambda ws: ws.on("framereceived", got.append))
    return got


def test_a_tool_row_loads_its_output_when_opened(server, page):
    frames = frames_on(page)
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page, "/bash list it => SECRET-OUT")
    assert page.locator(".row.tool pre.out").count() == 0, "the tail came unasked"
    assert not any('"tail"' in f for f in frames if '"kind": "tool"' in f)
    page.click(".row.tool summary")
    page.wait_for_selector(".row.tool pre.out >> text=SECRET-OUT")
    page.reload()
    page.wait_for_selector(".row.tool")
    page.click(".row.tool summary")
    page.wait_for_selector(".row.tool pre.out >> text=SECRET-OUT")
    assert page.errors == []


def test_an_open_row_refetches_when_its_result_lands(server, page):
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    spawn(page)
    page.fill("#input", "/sleep 2")
    page.press("#input", "Enter")
    page.wait_for_selector(".row.tool.running")
    page.click(".row.tool.running summary")
    turns_done(page, 1)
    page.wait_for_selector(".row.tool.ok pre.out", state="visible")
    assert page.errors == []


def test_returning_to_a_tab_receives_only_what_changed(server, page):
    frames = frames_on(page)
    page.goto(server.url)
    page.wait_for_selector("#a2[data-view=fleet]")
    a = spawn(page, "alpha " + "x" * 3000)
    spawn(page, "beta")
    rows = page.evaluate("document.querySelectorAll('#entries .row').length")
    frames.clear()
    page.click(f"#tablist .tab[data-id='{a}']")
    page.wait_for_function("id => location.hash === '#s=' + id", arg=a)
    page.wait_for_selector(".row.user >> text=alpha")
    snaps = [f for f in frames if '"t": "snapshot"' in f and f"transcript:{a}" in f]
    assert snaps and all('"since"' in f for f in snaps), snaps
    assert sum(map(len, snaps)) < 1500, [len(f) for f in snaps]
    assert page.evaluate("document.querySelectorAll('#entries .row').length") >= 3
    assert rows >= 3 and page.errors == []
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest -q -m browser -k "loads_its_output or refetches or returning_to_a_tab or spawn_to_close"`
Expected: FAIL: the page shows "This page and the server speak different protocol versions" until `PROTO` is 2 in the client, then the output test finds no `pre.out` and the return test finds no `"since"`.

- [ ] **Step 3: Implement `protocol.js`**

```js
export const PROTO = 2;
```

```js
  // since: optional () => the revision this subscriber holds, or null. A
  // channel that keeps revisions answers a resubscribe with what changed.
  subscribe(channel, onSnapshot, onPatch, onError, since) {
    this.subs.set(channel, { seq: 0, onSnapshot, onPatch, onError, since });
    if (this.open) this._sendSub(channel);
    return () => {
      this.subs.delete(channel);
      if (this.open) this.ws.send(JSON.stringify({ t: "unsub", channel }));
    };
  }

  _sendSub(channel) {
    const rev = this.subs.get(channel)?.since?.();
    const msg = { t: "sub", channel };
    if (Number.isInteger(rev)) msg.since = rev;
    this.ws.send(JSON.stringify(msg));
  }
```

Also update the file's header comment (line 4) to: `// numbers, or a reconnect, means resubscribe, saying which revision it holds.`

- [ ] **Step 4: Implement `transcript.js`**

Change the header comment's first paragraph to add one sentence at the end: `The data of a tab not on screen is kept by app.js (stash and restore), and a row the wire sent without its detail fetches it when it opens (transcript/wire.py).`

Constructor: take `loadDetail` and add the new state after `this.selected = null;`:

```js
  constructor(scroller, list, jump, loadDetail) {
    ...
    this.loadDetail = loadDetail; // ids -> Promise of whole entries
    this.rev = -1; // the highest revision seen: what a resubscribe asks since
    this.full = new Map(); // id -> the whole entry, fetched when its row opened
    this.fetching = new Set();
```

In the existing capture-phase `toggle` listener, fetch on open:

```js
    list.addEventListener(
      "toggle",
      (ev) => {
        const id = ev.target.closest(".row")?.dataset.id;
        if (id && this.touched.has(id)) this.opened.set(id, ev.target.open);
        if (id && ev.target.open) this.fetch(id);
      },
      true, // toggle does not bubble
    );
```

Replace `mount`, `snapshot`, `apply`'s upsert branch and `clear`, and add `whole`, `fetch`, `resume`, `stash` and `restore`:

```js
  // The entry to draw: the fetched whole one while it is still current.
  whole(e) {
    const f = this.full.get(e.id);
    return f && f.rev === e.rev ? f : e;
  }

  mount(e) {
    const n = render(this.whole(e));
    if (this.opened.has(e.id)) {
      const d = n.querySelector("details");
      if (d) d.open = this.opened.get(e.id);
      if (d?.open) this.fetch(e.id);
    }
    this.nodes.set(e.id, n);
    return n;
  }

  // A row opened: fetch what the wire left out, unless it is here and current.
  fetch(id) {
    const e = this.entries.get(id);
    if (!e?.detail?.more || this.whole(e) !== e || this.fetching.has(id)) return;
    this.fetching.add(id);
    this.loadDetail([id])
      .then((got) => {
        for (const f of got) {
          this.full.set(f.id, f);
          const cur = this.entries.get(f.id);
          const old = this.nodes.get(f.id);
          if (cur && old && cur.rev === f.rev) old.replaceWith(this.mount(cur));
        }
        this.mark();
      })
      .catch(() => {})
      .finally(() => this.fetching.delete(id));
  }

  snapshot(data) {
    this.clear();
    this.rev = data.rev;
    for (const e of data.entries) this.entries.set(e.id, e);
    const frag = document.createDocumentFragment();
    for (const e of data.entries.slice(-WINDOW)) frag.append(this.mount(e));
    this.list.replaceChildren(frag);
    this.mark();
    this.toBottom();
  }

  // A delta: what changed since the revision this tab held, removals first.
  resume(data) {
    this.apply([...data.removed.map((id) => ({ remove: id })), ...data.entries.map((e) => ({ upsert: e }))]);
    this.rev = Math.max(this.rev, data.rev);
  }

  // Hands over this tab's data and starts empty; restore() takes it back.
  stash() {
    const s = { entries: this.entries, full: this.full, opened: this.opened, touched: this.touched, rev: this.rev };
    this.entries = new Map();
    this.full = new Map();
    this.opened = new Map();
    this.touched = new Set();
    this.clear();
    return s;
  }

  restore(s) {
    this.clear();
    Object.assign(this, { entries: s.entries, full: s.full, opened: s.opened, touched: s.touched, rev: s.rev });
    const frag = document.createDocumentFragment();
    for (const e of [...this.entries.values()].slice(-WINDOW)) frag.append(this.mount(e));
    this.list.replaceChildren(frag);
    this.mark();
    this.toBottom();
  }
```

In `apply`, after `const e = op.upsert;` add `if (e.rev > this.rev) this.rev = e.rev;`.

In `clear`, add `this.full.clear();`, `this.fetching.clear();` and `this.rev = -1;`.

- [ ] **Step 5: Implement `entries.js`**

`tool`: delete `if (det.collapsed === false) d.open = true;`, and after `if (det.args) more.append(el("pre", "args", det.args));` add:

```js
    if (det.more) more.append(el("div", "loading", "loading…"));
```

`thinking`:

```js
  thinking(e) {
    const body = el("div", "body");
    if (e.md || e.detail?.more) {
      const d = el("details");
      d.append(el("summary", null, e.title || "Thinking"), e.md ? markdown(e.md) : el("div", "loading", "loading…"));
      body.append(d);
    } else {
      body.textContent = e.summary || "thought";
    }
    return row(e, "think", body);
  },
```

`system`: change `if (e.detail?.tail) {` to `if (e.detail?.tail || e.detail?.more) {` and the `pre` to `e.detail.tail ? el("pre", "out", e.detail.tail) : el("div", "loading", "loading…")`. Make the same two changes in `user` (its `"template"` details).

In `src/aegis/client/css/base.css`, next to the `.row.tool` rules, add:

```css
#a2 .row .loading{font-family:var(--font-mono);font-size:12px;color:var(--faint);padding:4px 0}
```

- [ ] **Step 6: Implement `app.js`**

Where `transcript` is constructed (`new Transcript(...)`), pass a loader that drops answers for a tab no longer shown:

```js
const transcript = new Transcript($("tr"), $("entries"), $("jump"), (ids) => {
  const id = shown;
  return conn.call("transcript.detail", { log_id: id, ids }).then((got) => (shown === id ? got : []));
});
```

Replace `follow`:

```js
// The last TAB_CACHE tabs left, newest last: a return to one shows it at once
// and asks only for what changed since (transcript/wire.py, Fold.snapshot).
const TAB_CACHE = 8;
const kept = new Map(); // log_id -> transcript.stash()

function follow(id) {
  if (shown === id) return;
  if (unsubTranscript) unsubTranscript();
  unsubTranscript = null;
  if (shown) {
    kept.delete(shown);
    kept.set(shown, transcript.stash());
    while (kept.size > TAB_CACHE) kept.delete(kept.keys().next().value);
  } else transcript.clear();
  shown = id;
  if (!id) return;
  const saved = kept.get(id);
  if (saved) {
    kept.delete(id);
    transcript.restore(saved);
  }
  unsubTranscript = conn.subscribe(
    `transcript:${id}`,
    (data) => {
      if (data.since !== undefined) transcript.resume(data);
      else transcript.snapshot(data);
      // Read by scripts/bench.py: when the snapshot was drawn and painted.
      const mark = (window.__a2snapshot = { at: performance.now(), count: transcript.entries.size });
      requestAnimationFrame(() => (mark.painted = performance.now()));
    },
    (ops) => transcript.apply(ops),
    undefined,
    () => transcript.rev,
  );
  menu.close();
  $("input").value = localStorage.getItem(`aegis.draft.${id}`) || "";
  autosize();
}
```

`transcript.entries` is read elsewhere in `app.js` only through `transcript` methods; check with `grep -n "transcript\." src/aegis/client/js/app.js` that no caller holds on to `transcript.entries` across a `follow`.

- [ ] **Step 7: Run the browser tests**

Run: `uv run pytest -q -m browser -k "loads_its_output or refetches or returning_to_a_tab or spawn_to_close or restart_brings or long_transcript or selected_row or j_k"`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/aegis/client/js/protocol.js src/aegis/client/js/transcript.js src/aegis/client/js/entries.js src/aegis/client/js/app.js src/aegis/client/css/base.css tests/test_browser.py
git commit -m "feat(client): keep the last 8 tabs, resume since their rev, load a row's detail on open"
```

### Task 5: Bench rows, DESIGN.md and the release note

**Files:**
- Modify: `scripts/bench.py` (`browser_runs`, the cold-load block)
- Modify: `DESIGN.md` ("The client knows no subsystem by name", "The patches add up to the entries"; new "The wire carries no collapsed detail")
- Create: `changelog.d/mobile-lighter-wire.performance.md`, `changelog.d/mobile-tool-rows-closed.changed.md`

**Interfaces:**
- Produces: bench metrics `cold_snapshot_kb` and `return_tab_kb` (lower is better; `scripts/bench_compare.py` needs no change).

- [ ] **Step 1: Add the bench rows**

In `scripts/bench.py`, in the cold-load block of `browser_runs`, record frames before the reloads and measure after them. Replace the `loads = []` loop and the `out.update(cold_load_ms=...)` call with:

```python
                frames: list[str] = []
                page.on(
                    "websocket", lambda ws: ws.on("framereceived", frames.append)
                )
                loads = []
                for _ in range(3):
                    frames.clear()
                    page.reload()
                    page.wait_for_function(
                        "window.__a2snapshot && window.__a2snapshot.painted",
                        timeout=60_000,
                    )
                    loads.append(page.evaluate("window.__a2snapshot.painted"))
                snap = [f for f in frames if '"t": "snapshot"' in f and "transcript:" in f]
                here = page.evaluate("location.hash")
                page.evaluate("location.hash = '#fleet'")
                page.wait_for_selector("#a2[data-view=fleet]")
                frames.clear()
                page.evaluate("h => (location.hash = h)", here)
                page.wait_for_function(
                    "document.querySelector('#a2').dataset.view === 'session'"
                )
                page.wait_for_timeout(500)
                back = [f for f in frames if "transcript:" in f]
                out.update(
                    cold_load_ms=min(loads),
                    cold_load_entries=page.evaluate("window.__a2snapshot.count"),
                    cold_snapshot_kb=sum(map(len, snap)) / 1000,
                    return_tab_kb=sum(map(len, back)) / 1000,
                    server_rss_mb=srv.rss_mb(),
                    browser_heap_mb=page.evaluate("performance.memory.usedJSHeapSize")
                    / 2**20,
                )
```

Run: `make bench`
Expected: the table prints `cold_snapshot_kb` (several hundred on `main`, a quarter of that here) and `return_tab_kb` (under 2). Keep the table for the PR body; run it on `origin/main` as well for the base column (`git stash` is banned here: use a second worktree).

- [ ] **Step 2: DESIGN.md**

In "The client knows no subsystem by name", change the second sentence to: `A gap in the numbers, or a reconnect, means resubscribe, saying which revision it holds; a channel that keeps revisions answers with what changed after it, and any other with a fresh snapshot.`

Append to "The patches add up to the entries": `A delta from any revision, applied to the entries as of that revision, equals the entries now, live ones included (tests/test_wire.py checks every cut of the fixtures and 40 earlier moments at every step of a stream).`

Add a rule after "Python decides, the browser draws":

```markdown
**The wire carries no collapsed detail.** A tool's arguments, output and diff, a
system note's tail and thinking text stay on the server until a row opens and
asks for them with `transcript.detail` (`transcript/wire.py`). They were 75% of a
snapshot's bytes over 80 real transcripts. Every entry carries `rev`, the store
index of the record that last changed it, so a returning client asks for what
changed since the revision it holds, and the client keeps the last 8 tabs it
showed.
```

- [ ] **Step 3: Release notes**

`changelog.d/mobile-lighter-wire.performance.md`:

```markdown
- **A transcript costs a quarter of the bytes, and a return to a tab costs only what changed.** Tool arguments, output, diffs and thinking text load when you open a row; they were 75% of a snapshot. Switching back to one of the last 8 tabs, or reconnecting after a phone slept, sends only the entries that changed since. The protocol is now version 2: reload any page left open across the upgrade.
```

`changelog.d/mobile-tool-rows-closed.changed.md`:

```markdown
- **A failed tool call starts closed, like every tool row.** Its red status and one-line result show on the closed row; open it for the output.
```

Run: `make changelog-check`
Expected: exit 0.

- [ ] **Step 4: The gates and a real browser**

Run: `make check` (rift's `lint-docs` runs locally only), then `uv run pytest -q -m browser`.
Expected: all pass. Then start `uv run aegis serve --root <a temp dir> --port 8794 -d` from this worktree, open the printed URL, run `/bash x => y` and `/sleep 3` in a session, open both rows, switch to a second tab and back, and watch the devtools Network panel's WS frames: the return frame carries `"since"` and is under 2 KB. `kill` the server.

- [ ] **Step 5: Commit**

```bash
git add scripts/bench.py DESIGN.md changelog.d/mobile-lighter-wire.performance.md changelog.d/mobile-tool-rows-closed.changed.md
git commit -m "docs: the wire carries no collapsed detail; bench the snapshot and a return to a tab"
```
