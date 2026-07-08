# Subagent View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Group `Task`-tool subagent events into collapsible inline
"ventanitas" in the TUI and web transcript, routed by `parent_tool_use_id`.

**Architecture:** Capture `parent_tool_use_id` in the stream parser and
round-trip it through the event codec. A `Task` `ToolUse` opens a container
keyed by its `tool_call_id`; events tagged with that id route into the
container; the `Task` tool_result closes it. TUI uses a new `SubagentBox`
widget; web groups client-side in `coalesce.js` + `renderEvent.js`.

**Tech Stack:** Python 3.13, Textual 8.x (TUI), vanilla ES modules (web),
pytest + `uv run`, node for `.mjs` unit tests.

## Global Constraints

- Python 3.13+; `uv run pytest` for Python, `node tests/web/<x>.test.mjs` for JS.
- TDD: failing test first, minimal impl, commit per logical unit.
- One visual nesting level; deeper nesting falls back to inline rendering.
- Backward-compatible: events without `parent_tool_use_id` render exactly as
  today (field defaults to `None`).
- Collapse state is UI-only, never persisted; replay starts collapsed.
- A `SubagentBox` counts as ONE block in the TUI windowing (`_history` /
  `_mounted_blocks`); web groups without adding top-level records.
- Match existing style; touch only what the task needs.

---

### Task 1: Capture `parent_tool_use_id` in parser + event dataclasses

**Files:**
- Modify: `src/aegis/events.py` (dataclasses `AssistantText`,
  `AssistantThinking`, `ToolUse`, `ToolResult`, `AgentPlan`; `parse()`)
- Test: `tests/test_events_parent_id.py` (create)

**Interfaces:**
- Produces: `AssistantText`, `AssistantThinking`, `ToolUse`, `ToolResult`,
  `AgentPlan` each gain field `parent_tool_use_id: str | None = None`.
  `parse(line, state)` sets it from the stream message's
  `parent_tool_use_id` (None/absent → `None`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_events_parent_id.py
from aegis.events import parse, ToolUse, AssistantText, ToolResult


def _line(obj):
    import json
    return json.dumps(obj)


def test_assistant_tool_use_carries_parent_id():
    line = _line({
        "type": "assistant",
        "parent_tool_use_id": "toolu_PARENT",
        "message": {"id": "msg_1", "content": [
            {"type": "tool_use", "id": "toolu_child", "name": "Read",
             "input": {"file_path": "x.py"}}]},
    })
    ev = parse(line)
    assert isinstance(ev, ToolUse)
    assert ev.parent_tool_use_id == "toolu_PARENT"


def test_assistant_text_parent_absent_is_none():
    line = _line({
        "type": "assistant",
        "message": {"id": "m", "content": [{"type": "text", "text": "hi"}]},
    })
    ev = parse(line)
    assert isinstance(ev, AssistantText)
    assert ev.parent_tool_use_id is None


def test_tool_result_carries_parent_id():
    line = _line({
        "type": "user",
        "parent_tool_use_id": "toolu_PARENT",
        "message": {"content": [
            {"type": "tool_result", "tool_use_id": "toolu_child",
             "content": "ok", "is_error": False}]},
    })
    ev = parse(line)
    assert isinstance(ev, ToolResult)
    assert ev.parent_tool_use_id == "toolu_PARENT"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_events_parent_id.py -q`
Expected: FAIL — `TypeError: ... unexpected keyword` is NOT raised yet;
instead `AttributeError`/assertion (field missing / not set).

- [ ] **Step 3: Add the field to the five dataclasses**

In `src/aegis/events.py`, add `parent_tool_use_id: str | None = None` as the
LAST field of each dataclass (keeps positional back-compat):
`AssistantText`, `AssistantThinking`, `ToolUse`, `ToolResult`, `AgentPlan`.

Example (`ToolUse`):

```python
@dataclass
class ToolUse:
    name: str
    summary: str
    usage: TokenUsage | None = None
    kind: str | None = None
    raw_input: dict | None = None
    tool_call_id: str | None = None
    locations: tuple[tuple[str, int | None], ...] = ()
    status: str | None = None
    parent_tool_use_id: str | None = None
```

- [ ] **Step 4: Stamp the parent id in `parse()`**

In `parse()`, after `obj = json.loads(line)` succeeds, compute once:

```python
parent = obj.get("parent_tool_use_id")
```

Then pass `parent_tool_use_id=parent` to each of the five constructors:
the `AssistantText` (text branch), `AssistantThinking` (thinking branch),
`AgentPlan` (TodoWrite branch), `ToolUse` (tool_use branch), and
`ToolResult` (user/tool_result branch). Example for `ToolResult`:

```python
return ToolResult(
    text=text,
    is_error=bool(block.get("is_error", False)),
    tool_call_id=tcid,
    kind=kind,
    diff=diff,
    parent_tool_use_id=parent,
)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_events_parent_id.py -q`
Expected: PASS (3 passed)

- [ ] **Step 6: Guard against regressions in the existing parser tests**

Run: `uv run python -m pytest tests/test_events.py -q` (and any parser
fixture test)
Expected: PASS — no signature breakage.

- [ ] **Step 7: Commit**

```bash
git add src/aegis/events.py tests/test_events_parent_id.py
git commit -m "feat(events): capture parent_tool_use_id on nested events"
```

---

### Task 2: Round-trip `parent_tool_use_id` through the event codec

**Files:**
- Modify: `src/aegis/state/event_codec.py` (`encode_event`, `decode_event`)
- Test: `tests/test_event_codec_parent_id.py` (create)

**Interfaces:**
- Consumes: the `parent_tool_use_id` field from Task 1.
- Produces: `encode_event(ev)` includes `"parent_tool_use_id"` iff set;
  `decode_event(d)` restores it (defaults `None`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_event_codec_parent_id.py
from aegis.events import ToolUse, AssistantText
from aegis.state.event_codec import encode_event, decode_event


def test_parent_id_round_trips_when_set():
    ev = ToolUse(name="Read", summary="x", tool_call_id="c",
                 parent_tool_use_id="toolu_P")
    d = encode_event(ev)
    assert d["parent_tool_use_id"] == "toolu_P"
    assert decode_event(d).parent_tool_use_id == "toolu_P"


def test_parent_id_absent_when_none():
    d = encode_event(AssistantText(text="hi"))
    assert "parent_tool_use_id" not in d
    assert decode_event(d).parent_tool_use_id is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_event_codec_parent_id.py -q`
Expected: FAIL — key absent / attribute not restored.

- [ ] **Step 3: Encode the field**

In `encode_event`, for each of the five event types (`AssistantText`,
`AssistantThinking`, `ToolUse`, `ToolResult`, `AgentPlan`), after building
`out`, add:

```python
if ev.parent_tool_use_id is not None:
    out["parent_tool_use_id"] = ev.parent_tool_use_id
```

(Place it just before the `return out` of each branch. For `AgentPlan`,
which currently returns a dict literal, assign to a local `out` first.)

- [ ] **Step 4: Decode the field**

In `decode_event`, for each of the five branches, pass
`parent_tool_use_id=d.get("parent_tool_use_id")` to the constructor.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_event_codec_parent_id.py tests/test_event_codec.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/aegis/state/event_codec.py tests/test_event_codec_parent_id.py
git commit -m "feat(codec): round-trip parent_tool_use_id"
```

---

### Task 3: TUI `SubagentBox` widget (render only, no wiring)

**Files:**
- Modify: `src/aegis/tui/pane.py` (add `SubagentBox` near `CopyableBlock`)
- Test: `tests/test_subagent_box.py` (create)

**Interfaces:**
- Consumes: `render_event`, `_payload_for_event` (module-level in `pane.py`);
  the palette object.
- Produces:
  `SubagentBox(header_renderable, header_payload, palette, *, collapsed=True)`
  with:
  - `add_child(renderable, payload, *, tight=False)` — append a child record.
  - `fold_child_result(renderable, payload)` — fold a result into the last
    child (mirror of `_fold_tool_result`, used for tool pairs inside the box).
  - `close(status_renderable, status_payload)` — mark done + set footer.
  - reactive `collapsed: bool`; `toggle()`; header re-renders live via
    `set_header(renderable, payload)`.
  - `text_payload() -> str` (full box payload, like `CopyableBlock`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_subagent_box.py
import pytest
from rich.text import Text
from aegis.tui.pane import SubagentBox
from aegis.tui.themes import INK, aegis_colors


def _pal():
    return aegis_colors(INK)


@pytest.mark.asyncio
async def test_box_collapsed_shows_header_only_expanded_shows_children():
    from textual.app import App
    from textual.widgets import Static

    class Host(App):
        def compose(self):
            box = SubagentBox(Text("🤖 Task(explore)"), "Task(explore)",
                              _pal(), collapsed=True)
            box.add_child(Text("⏺ Read(a.py)"), "Read(a.py)")
            box.add_child(Text("  └ ok done"), "ok done")
            self.box = box
            yield box

    app = Host()
    async with app.run_test():
        box = app.box
        # Collapsed: children not composed into the body.
        assert box.collapsed is True
        assert "Read(a.py)" in box.text_payload()  # payload still complete
        box.toggle()
        assert box.collapsed is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_subagent_box.py -q`
Expected: FAIL — `ImportError: cannot import name 'SubagentBox'`.

- [ ] **Step 3: Implement `SubagentBox`**

Add to `src/aegis/tui/pane.py` (after `CopyableBlock`). It renders a header
row plus, when expanded, an indented body (children) and a footer. Store
child records so the payload and expanded body stay complete.

```python
from textual.reactive import reactive


class SubagentBox(Widget):
    """Collapsible container for one Task subagent's events. Header is the
    Task call; body is the routed child events; footer is the Task result."""

    DEFAULT_CSS = """
    SubagentBox { height: auto; padding: 0 1; margin-bottom: 1;
                  background: $background; }
    SubagentBox > .sa-header { height: auto; }
    SubagentBox > .sa-body { height: auto; padding: 0 0 0 2;
                             border-left: solid $surface; }
    SubagentBox:hover { background: $surface; }
    """

    collapsed: reactive[bool] = reactive(True)

    def __init__(self, header, header_payload, palette, *,
                 collapsed: bool = True) -> None:
        super().__init__()
        self._palette = palette
        self._header = header
        self._header_payload = header_payload
        self._children: list[BlockRecord] = []
        self._footer: RenderableType | None = None
        self._footer_payload = ""
        self.set_reactive(SubagentBox.collapsed, collapsed)

    def set_header(self, renderable, payload) -> None:
        self._header = renderable
        self._header_payload = payload
        self._refresh()

    def add_child(self, renderable, payload, *, tight: bool = False) -> None:
        self._children.append(BlockRecord(renderable, payload, tight))
        self._refresh()

    def fold_child_result(self, renderable, payload) -> bool:
        if not self._children:
            return False
        rec = self._children[-1]
        rec.renderable = Group(rec.renderable, renderable)
        rec.payload = f"{rec.payload}\n{payload}"
        self._refresh()
        return True

    def close(self, renderable, payload) -> None:
        self._footer = renderable
        self._footer_payload = payload
        self._refresh()

    def toggle(self) -> None:
        self.collapsed = not self.collapsed

    def watch_collapsed(self, _old: bool, _new: bool) -> None:
        self._refresh()

    def text_payload(self) -> str:
        parts = [self._header_payload]
        parts += [c.payload for c in self._children]
        if self._footer_payload:
            parts.append(self._footer_payload)
        return "\n".join(p for p in parts if p)

    def compose(self) -> ComposeResult:
        yield Static(self._header, classes="sa-header")
        yield Static(self._body_renderable(), classes="sa-body")

    def _body_renderable(self) -> RenderableType:
        if self.collapsed:
            return Text("")
        rends: list[RenderableType] = [c.renderable for c in self._children]
        if self._footer is not None:
            rends.append(self._footer)
        return Group(*rends) if rends else Text("")

    def _refresh(self) -> None:
        import contextlib
        with contextlib.suppress(Exception):
            self.query_one(".sa-header", Static).update(self._header)
        with contextlib.suppress(Exception):
            self.query_one(".sa-body", Static).update(self._body_renderable())

    def on_click(self, event) -> None:
        self.toggle()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_subagent_box.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/aegis/tui/pane.py tests/test_subagent_box.py
git commit -m "feat(tui): SubagentBox collapsible container widget"
```

---

### Task 4: Wire subagent routing into `ConversationPane`

**Files:**
- Modify: `src/aegis/tui/pane.py` (`__init__`, `_on_core_event`,
  `_mount_replay`, `_mount_block` return path)
- Test: `tests/test_pane_subagent.py` (create)

**Interfaces:**
- Consumes: `SubagentBox` (Task 3); events carrying `parent_tool_use_id`
  (Task 1).
- Produces: routing so a `Task` `ToolUse` mounts a `SubagentBox`, child
  events land inside it, and the Task `ToolResult` closes it. State:
  `self._subagent_boxes: dict[str, SubagentBox]` and
  `self._subagent_header_state: dict[str, tuple[str, int]]`
  (tool_call_id → (summary, child_count)) for the live header.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pane_subagent.py
import pytest
from aegis.config import Agent
from aegis.events import ToolUse, ToolResult, AssistantText
from aegis.tui.app import AegisApp
from aegis.tui.pane import SubagentBox, CopyableBlock


def _agent():
    return Agent(harness="claude-code", model="opus", effort="high",
                 permission="auto")


class _FakeSession:
    def __init__(self): self.sent = []; self.started = self.closed = False
    async def start(self): self.started = True
    async def send(self, t): self.sent.append(t)
    async def events(self):
        if False: yield
    async def close(self): self.closed = True


class _FakeMCP:
    url = "http://127.0.0.1:0/mcp/"
    def __init__(self): self.started = self.stopped = False; self.bound = None
    def bind(self, b): self.bound = b
    async def start(self): self.started = True
    async def stop(self): self.stopped = True


def _app():
    return AegisApp({"default": _agent()}, "default",
                    lambda a, u, h: _FakeSession(), _FakeMCP())


@pytest.mark.asyncio
async def test_task_children_group_into_a_subagent_box():
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        for b in list(pane.query(CopyableBlock)):
            b.remove()
        pane._history.clear(); pane._mounted_blocks.clear()
        pane._window_start = 0; pane._tool_use_idx.clear()
        pane._subagent_boxes.clear()

        # Task dispatch opens a box.
        pane._on_core_event(None, ToolUse(
            name="Task", summary="explore X", kind="think", tool_call_id="T1"))
        # Child events routed by parent_tool_use_id.
        pane._on_core_event(None, AssistantText(
            text="looking…", parent_tool_use_id="T1"))
        pane._on_core_event(None, ToolUse(
            name="Read", summary="a.py", kind="read", tool_call_id="c1",
            parent_tool_use_id="T1"))
        pane._on_core_event(None, ToolResult(
            text="file body", is_error=False, tool_call_id="c1",
            parent_tool_use_id="T1"))
        # Task result closes the box.
        pane._on_core_event(None, ToolResult(
            text="subagent done", is_error=False, tool_call_id="T1"))
        await pilot.pause()

        # Exactly one top-level block: the SubagentBox (children live inside).
        assert len(pane._history) == 1
        box = pane._subagent_boxes["T1"]
        assert isinstance(box, SubagentBox)
        payload = box.text_payload()
        assert "explore X" in payload          # header
        assert "looking" in payload            # child text
        assert "a.py" in payload               # child tool use
        assert "file body" in payload          # child tool result (folded)
        assert "subagent done" in payload      # footer


@pytest.mark.asyncio
async def test_child_without_known_box_falls_back_inline():
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        for b in list(pane.query(CopyableBlock)):
            b.remove()
        pane._history.clear(); pane._mounted_blocks.clear()
        pane._window_start = 0; pane._tool_use_idx.clear()
        pane._subagent_boxes.clear()
        pane._on_core_event(None, AssistantText(
            text="orphan child", parent_tool_use_id="UNKNOWN"))
        await pilot.pause()
        assert len(pane._history) == 1  # rendered inline, not dropped
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_pane_subagent.py -q`
Expected: FAIL — `AttributeError: ... _subagent_boxes`.

- [ ] **Step 3: Add state in `ConversationPane.__init__`**

Next to `self._tool_use_idx`:

```python
# Task tool_call_id → its SubagentBox. Events tagged with a known
# parent_tool_use_id route into the matching box instead of the transcript.
self._subagent_boxes: dict[str, SubagentBox] = {}
self._subagent_counts: dict[str, int] = {}
self._subagent_summary: dict[str, str] = {}
```

- [ ] **Step 4: Route in `_on_core_event`**

Rewrite the dispatch order. `_mount_block` must return the mounted widget
(it already returns the `CopyableBlock`). Insert, BEFORE the existing
AssistantText/ToolResult handling:

```python
def _on_core_event(self, _core, ev) -> None:
    parent = getattr(ev, "parent_tool_use_id", None)
    if parent and parent in self._subagent_boxes:
        self._route_into_box(parent, ev)
        self.refresh_metrics()
        return
    if isinstance(ev, ToolResult) and ev.tool_call_id in self._subagent_boxes:
        self._close_box(ev.tool_call_id, ev)     # Task result closes its box
        self.refresh_metrics()
        return
    if isinstance(ev, ToolUse) and ev.name == "Task" and ev.tool_call_id:
        self._open_box(ev)
        self.refresh_metrics()
        return
    # ... existing AssistantText / AssistantThinking / ToolResult-fold /
    #     default-mount branches unchanged ...
```

Add the helpers:

```python
def _open_box(self, ev: ToolUse) -> None:
    self._flush_streaming()
    header = self._box_header(ev, running=True, count=0)
    box = SubagentBox(header, _payload_for_event(ev), self._palette)
    # Mount as ONE transcript block, reusing the windowing bookkeeping.
    self._history.append(BlockRecord(header, _payload_for_event(ev), False))
    t = self._transcript()
    ind = self._working_indicator()
    if ind is not None and ind.parent is t:
        t.mount(box, before=ind)
    else:
        t.mount(box)
    self._mounted_blocks.append(box)
    self._subagent_boxes[ev.tool_call_id] = box
    self._subagent_counts[ev.tool_call_id] = 0
    self._subagent_summary[ev.tool_call_id] = ev.summary or ev.name
    if self._stick_to_bottom:
        t.scroll_end(animate=False)

def _route_into_box(self, tid: str, ev) -> None:
    box = self._subagent_boxes[tid]
    if isinstance(ev, ToolResult) and box.fold_child_result(
            render_event(ev, self._palette), _payload_for_event(ev)):
        pass
    else:
        r = render_event(ev, self._palette)
        if r is not None:
            box.add_child(r, _payload_for_event(ev),
                          tight=isinstance(ev, ToolUse))
    self._subagent_counts[tid] += 1
    box.set_header(self._box_header_running(tid), box._header_payload)
    if self._stick_to_bottom:
        self._transcript().scroll_end(animate=False)

def _close_box(self, tid: str, ev: ToolResult) -> None:
    box = self._subagent_boxes[tid]
    icon = "✗" if ev.is_error else "✓"
    box.set_header(self._box_header_done(tid, icon), box._header_payload)
    box.close(render_event(ev, self._palette), _payload_for_event(ev))

def _box_header(self, ev: ToolUse, *, running: bool, count: int) -> Text:
    return Text.assemble(("🤖 ", self._palette.accent),
                         f"{ev.summary or ev.name} · ⏳ {count} events")

def _box_header_running(self, tid: str) -> Text:
    n = self._subagent_counts[tid]
    s = self._subagent_summary[tid]
    return Text.assemble(("🤖 ", self._palette.accent),
                         f"{s} · ⏳ {n} events")

def _box_header_done(self, tid: str, icon: str) -> Text:
    n = self._subagent_counts[tid]
    s = self._subagent_summary[tid]
    return Text.assemble(("🤖 ", self._palette.accent),
                         f"{s} · {icon} {n} events")
```

Note: children whose own tool_use/tool_result pair (e.g. the subagent runs
Read → result) fold *inside* the box via `fold_child_result`, keeping
per-call pairing consistent with the top level.

- [ ] **Step 5: Reconstruct boxes in `_mount_replay`**

In the coalesced build loop, before the existing `ToolResult`-fold and
append logic, add the same routing against a local
`boxes: dict[str, list[BlockRecord]]` — but simplest: build boxes as
`SubagentBox` records too. Concretely, track `open_box: dict[str, int]`
(tool_call_id → index of the box's BlockRecord) and, for an event with
`parent_tool_use_id in open_box`, append its rendered child into that box's
record via `Group` (mirror `_route_into_box` but on records). A `Task`
`ToolUse` appends a box record and registers it; its `ToolResult` folds as
footer. Reuse `Group` exactly as the existing result-fold does. Keep the
`BlockRecord` count = one per box.

```python
# inside _mount_replay's coalesce loop, add at the top:
if isinstance(ev, ToolResult) and ev.tool_call_id in open_box:
    idx = open_box.pop(ev.tool_call_id)          # Task result → footer
    rec = records[idx]
    r = render_event(ev, self._palette)
    if r is not None:
        rec.renderable = Group(rec.renderable, r)
        rec.payload = f"{rec.payload}\n{_payload_for_event(ev)}"
    continue
p = getattr(ev, "parent_tool_use_id", None)
if p and p in box_idx:
    rec = records[box_idx[p]]
    r = render_event(ev, self._palette)
    if r is not None:
        rec.renderable = Group(rec.renderable, r)
        rec.payload = f"{rec.payload}\n{_payload_for_event(ev)}"
    continue
# ... then existing ToolResult-fold / append; when appending a Task ToolUse:
if isinstance(ev, ToolUse) and ev.name == "Task" and ev.tool_call_id:
    box_idx[ev.tool_call_id] = len(records) - 1
    open_box[ev.tool_call_id] = len(records) - 1
```

(Declare `box_idx: dict[str, int] = {}` and `open_box: dict[str, int] = {}`
alongside `use_idx` at the top of the method. Replay renders the flattened
box content into one record; live interactivity via `SubagentBox` applies to
new turns — replayed boxes render expanded-flattened, which is acceptable for
resumed history.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_pane_subagent.py tests/test_pane_windowing.py tests/test_pane_replay.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/aegis/tui/pane.py tests/test_pane_subagent.py
git commit -m "feat(tui): route Task subagent events into SubagentBox"
```

---

### Task 5: Web — route child frames onto the parent Task record

**Files:**
- Modify: `src/aegis/web/static/js/coalesce.js`
- Test: `tests/web/coalesce.test.mjs` (extend)

**Interfaces:**
- Consumes: frames whose compact `event.parent_tool_use_id` is set (Task 2
  makes the server include it).
- Produces: a `ToolUse` record with `name === "Task"` grows a `.children`
  array; a child frame returns `{action:"update", index:<task record>}`.

- [ ] **Step 1: Write the failing test (append to coalesce.test.mjs)**

```javascript
// 9) subagent children route onto their Task record's .children
{
  const task = (id, seq) => ({
    type: "stream", kind: "event", handle: "h", seq,
    event_type: "ToolUse",
    event: { t: "ToolUse", name: "Task", summary: "explore", tool_call_id: id },
  });
  const child = (parent, type, ev, seq) => ({
    type: "stream", kind: "event", handle: "h", seq,
    event_type: type, event: { t: type, ...ev, parent_tool_use_id: parent },
  });
  const history = [];
  coalesceInto(history, task("T1", 1));
  const r = coalesceInto(history, child("T1", "ToolUse",
    { name: "Read", tool_call_id: "c1" }, 2));
  assert.equal(r.action, "update");
  assert.equal(r.index, 0);
  assert.equal(history.length, 1);              // no top-level child block
  assert.equal(history[0].children.length, 1);
  assert.equal(history[0].children[0].event.name, "Read");
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node tests/web/coalesce.test.mjs`
Expected: FAIL (assertion on `children`).

- [ ] **Step 3: Implement routing in `coalesceInto`**

Add BEFORE the tool-result fold block:

```javascript
// Route a subagent's event into its Task record's children (grouped view).
const parentId = ev.parent_tool_use_id ?? null;
if (parentId !== null) {
  for (let i = history.length - 1; i >= 0; i--) {
    const b = history[i];
    if (b.event_type === "ToolUse"
        && (b.event || {}).name === "Task"
        && (b.event || {}).tool_call_id === parentId) {
      (b.children ||= []).push({
        seq: frame.seq, event_type: eventType, event: ev,
        truncated: frame.truncated ?? false, handle: frame.handle,
      });
      // In-box tool pairing: fold a child result into the prior child use.
      if (eventType === "ToolResult" && ev.tool_call_id != null
          && b.children.length >= 2) {
        const prev = b.children[b.children.length - 2];
        if (prev.event_type === "ToolUse"
            && (prev.event || {}).tool_call_id === ev.tool_call_id) {
          prev.result = ev;
          prev.resultSeq = frame.seq;
          prev.resultTruncated = frame.truncated ?? false;
          b.children.pop();  // fold rather than keep as separate child
        }
      }
      return { action: "update", index: i };
    }
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `node tests/web/coalesce.test.mjs`
Expected: `coalesce.test.mjs: all assertions passed`

- [ ] **Step 5: Commit**

```bash
git add src/aegis/web/static/js/coalesce.js tests/web/coalesce.test.mjs
git commit -m "feat(web): route subagent frames into Task .children"
```

---

### Task 6: Web — render the collapsible subagent box + toggle + styling

**Files:**
- Modify: `src/aegis/web/static/js/renderEvent.js`,
  `src/aegis/web/static/js/app.js`, `src/aegis/web/static/css/base.css`
- Test: `tests/web/renderEvent.test.mjs` (extend)

**Interfaces:**
- Consumes: a `ToolUse` record with `.children` (Task 5), and the existing
  `toolResultHtml`/folded-result rendering (commit `8125861`).
- Produces: `renderEvent` returns `<div class="subagent" data-collapsed>` for
  a `Task` `ToolUse` with children; `app.js` toggles `data-collapsed` on
  header click and re-renders the node when children arrive.

- [ ] **Step 1: Write the failing test (append to renderEvent.test.mjs)**

```javascript
// Task with children → collapsible subagent box (header + hidden body)
{
  const html = renderEvent(rec("ToolUse",
    { t: "ToolUse", name: "Task", summary: "explore X", tool_call_id: "T1" },
    { children: [
        { event_type: "ToolUse", handle: "h", seq: 2,
          event: { t: "ToolUse", name: "Read", summary: "a.py" } },
      ] }));
  assert.ok(html.includes("subagent"));
  assert.ok(html.includes("subagent-header"));
  assert.ok(html.includes("data-collapsed"));
  assert.ok(html.includes("explore X"));
  assert.ok(html.includes("Read"));            // child rendered in body
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node tests/web/renderEvent.test.mjs`
Expected: FAIL (no `subagent`).

- [ ] **Step 3: Render the box in `renderEvent.js`**

In the `ToolUse` branch, BEFORE the folded-result return, handle children:

```javascript
if (rec.children && rec.children.length) {
  const n = rec.children.length;
  const header = `<div class="subagent-header">🤖 `
    + `<span class="tool-name">${esc(ev.summary || ev.name)}</span> `
    + `<span class="sa-count">· ${n} events</span></div>`;
  const body = rec.children.map((c) => renderEvent(c)).join("");
  return `<div class="subagent" data-collapsed>${header}`
    + `<div class="subagent-body">${body}</div></div>`;
}
```

(`renderEvent(c)` recurses; a child ToolUse with a folded `.result` renders
its `.tool-call` pair via the existing path.)

- [ ] **Step 4: Toggle + live re-render in `app.js`**

In `renderInto`, the `ToolUse` update branch already re-renders the node
(commit `8125861`) — confirm children trigger the same `blockEl(rec)`
replace. Add a delegated click handler (near the existing `panesEl`
expand-on-tap listener):

```javascript
panesEl.addEventListener("click", (e) => {
  const head = e.target.closest(".subagent-header");
  if (!head) return;
  const box = head.closest(".subagent");
  if (box) box.toggleAttribute("data-collapsed");
});
```

- [ ] **Step 5: Style in `base.css`**

```css
.subagent { border-left: 2px solid var(--aegis-surface); padding-left: 0.5rem; }
.subagent-header { cursor: pointer; color: var(--aegis-accent); }
.subagent .sa-count { color: var(--aegis-muted); }
.subagent[data-collapsed] .subagent-body { display: none; }
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `node tests/web/renderEvent.test.mjs && node tests/web/coalesce.test.mjs`
Expected: both pass.

- [ ] **Step 7: Commit**

```bash
git add src/aegis/web/static/js/renderEvent.js src/aegis/web/static/js/app.js src/aegis/web/static/css/base.css tests/web/renderEvent.test.mjs
git commit -m "feat(web): collapsible subagent box (render + toggle + css)"
```

---

### Task 7: Web protocol — subagent grouping survives resume

**Files:**
- Test: `tests/test_web_protocol.py` (extend)

**Interfaces:**
- Consumes: server history replay (`event_frame`) now carrying
  `parent_tool_use_id` in the compact event (Task 2).

- [ ] **Step 1: Write the failing/first test**

```python
async def test_subagent_parent_id_survives_history_replay(tmp_path):
    from aegis.events import ToolUse, AssistantText
    sd = tmp_path / "state"
    append_event(sd, "h", ToolUse(name="Task", summary="explore",
                                   tool_call_id="T1"))
    append_event(sd, "h", AssistantText(text="child", parent_tool_use_id="T1"))
    mgr = FakeManager({"h": FakeCore("h")})
    t, reg, task = await _run_authed(tmp_path, mgr, cores_state_dir=sd)
    t.feed({"type": "subscribe",
            "target": {"kind": "session", "handle": "h"}})
    await _settle()
    events = [s for s in t.sent if s.get("kind") == "event"]
    child = [e for e in events if e["event"].get("t") == "AssistantText"][-1]
    assert child["event"]["parent_tool_use_id"] == "T1"
    t.disconnect()
    await task
```

- [ ] **Step 2: Run test**

Run: `uv run python -m pytest tests/test_web_protocol.py::test_subagent_parent_id_survives_history_replay -q`
Expected: PASS (Task 2's codec already carries the field; this locks it in).

- [ ] **Step 3: Full regression sweep**

Run:
```
uv run python -m pytest -q -m "not live"
for f in tests/web/*.test.mjs; do node "$f" || exit 1; done
```
Expected: all pass (allow the known TUI/inotify flake — re-run failing
windowing tests in isolation to confirm).

- [ ] **Step 4: Commit**

```bash
git add tests/test_web_protocol.py
git commit -m "test(web): subagent parent_tool_use_id survives resume"
```

---

## Notes / known limitations

- The web fresh-open tail slice (`_tail_lower_seq`, REPLAY_TAIL) counts raw
  blocks and is unaware of subagent grouping, so a very long subagent could
  be split across the tail boundary on a *fresh* web open. Resume replay
  (this plan's Task 7) is unaffected. Tail-awareness of boxes is out of scope;
  revisit only if it bites in practice.
- Replayed subagent boxes in the TUI render flattened/expanded (one record
  with grouped content); live interactivity (collapse/expand) applies to
  boxes created during the running session. Acceptable per spec §5.
