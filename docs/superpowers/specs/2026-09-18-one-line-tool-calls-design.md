# One line per tool call, and a window behind it

*Design — 2026-09-18.*

*Status: implemented 2026-09-18, `bb50d70`..`52d4280`.*

Scope is the Textual TUI. `src/aegis/web/` is superseded by the
TUI-over-web refactor (stage 6 of
`2026-09-07-retire-web-ui-tui-over-web-design.md`, deletion not yet
planned) and gets nothing from this design — no wire change, no
`get_event`, no JS.

## The problem

A tool call occupies between two and a dozen transcript rows, and the extra
rows carry almost nothing.

```
⌬ run the test suite  ·  uv run pytest -x -q tests/  · 12.4s
  └ ok 3629 passed, 1 xfailed in 11.82s
✏️ edit pane.py: running = not track.done  · 0.2s
  ┌ src/aegis/tui/pane.py
  │ - running = not track.done
  │ + running = not track.done and not track.frozen
  └
📖 read render.py  · 0.3s
  └ ok      1→from __future__ import annotations
🔎 grep 'expand' in pane.py
  └ ok 519:    def on_click(self, event: Click) -> None:
```

Twelve rows for four calls. The second row of each pair is
`render_event`'s `ToolResult` branch (`render.py:299`), which prints the
first 100 characters of the output — the position where a Read shows its
first line of imports and a Bash shows a progress bar. The diff rows are
`_render_diff` (`render.py:155`), capped at six. Clicking a tool block
appends its arguments as more muted rows in the same block
(`render_tool_use(..., expanded=True)`), so the gesture that is supposed
to reveal detail makes the transcript longer rather than opening
anything.

The full text is not lost — `rec.payload` already carries it for the
clipboard — but nothing shows it.

## What it becomes

One row per call: label, verdict and digest, elapsed right-aligned.

```
⌬ run the test suite            ⠹ 3.2s
⌬ run the test suite            ✓ 3629 passed, 1 xfailed         12.4s
✏️ edit pane.py                  ✓ +12 −3                          0.2s
📖 read render.py               ✓ 501 lines                       0.3s
🔎 grep 'expand' in pane.py     ✗ no matches                      0.1s
⏺ mcp__aegis__aegis_monitor     ✓ monitor_id: mon_4f2a             0.1s
```

Clicking opens the call in a modal with its full input and full output.

## The line

`render_tool_use` (`render.py:121`) grows two parameters: `width` (to
right-align the elapsed column, the way `render_user_line` already takes
one) and `result` (the folded `ToolResult` event, or `None` while the
call is in flight). Its `expanded` parameter goes away with the inline
expansion it served.

Three columns:

| column | content | width |
|---|---|---|
| label | `tool_glyph` + `describe_tool`, unchanged | flexible, clipped with `…` |
| verdict | `✓` / `✗` / spinner, then the digest | flexible, clipped |
| elapsed | `_fmt_dur`, right-aligned | 7 |

The label keeps `describe_tool`, not `tool_label`. `tool_label` exists
for the fleet dashboard's activity tail, where a Bash command is noise;
in a transcript the command is what you scan for when something broke,
and it is the second half of the Bash label.

While a call runs there is no verdict and no digest — spinner and
elapsed, as today. The elapsed no longer hides below 1s: a right-hand
column that appears and disappears jitters, and a `0.3s` read is
information.

### The digest

A new pure function in `render_shared.py`, beside `describe_tool`:

```python
def result_digest(name: str, result: ToolResult) -> str
```

| tool | digest | source |
|---|---|---|
| `Edit`, `Write` | `+12 −3` | `ToolResult.diff`, counted in full |
| `Read` | `501 lines` | line count of `result.text` |
| `Grep`, `Glob` | `no matches` / `47 matches` | line count |
| `Bash` | last non-empty output line | — |
| anything else | first non-empty line | — |

`diff` counts are counted over the whole `(old_text, new_text)` pair, not
through `diff_window` — that helper elides the common prefix and caps the
window at six rows, which is right for a preview and wrong for a count.
A separate `diff_counts(old, new) -> tuple[int, int]` does the counting.

Only `Edit` and `Write` carry a diff — `events.py:670` records those two
and nothing else, and `Write`'s old side is empty, so a write reads
`+120`. Any other tool with no diff falls to the generic rule.

Bash is a judgment call. Its verdict is almost always on the last line —
`3629 passed`, `Successfully installed`, `error: cannot find` — and
first-line-wins would show `........ [ 1%]`. The cost is that `ls` and
`cat` show their last line rather than their first, which is noise and
not a lie. The real output is one click away.

On `is_error`, the glyph is `✗` in `colors.err` and the digest is the
first non-empty line of `result.text` whatever the tool, because an error
does not have the shape the tool's success digest reads.

### What stops rendering

- `render_event`'s `ToolResult` branch keeps existing, but only serves
  the orphan case: a result whose call is not in the pane (the call
  scrolled out of the replay window, or the harness sent a result for a
  call it never announced). `_fold_tool_result` returning `False` is
  exactly that case and already falls through to it.
- `_render_diff` is no longer called from the folded block. It moves into
  the modal, called with no line budget.
- `_ToolTrack.expanded`, the `expanded` parameter, and the re-render in
  `on_copyable_block_tool_expand_toggle` go away. The message itself
  stays: it is what the click posts, and it now opens the modal.

## The window

`ToolDetailScreen(ModalScreen)`, in a new `src/aegis/tui/tool_detail.py`.
House style: `DEFAULT_CSS` on the class, `border: round $panel`,
`background: $surface`, a muted help line docked at the bottom.

```
┌─ ⌬ run the test suite ─────────────────────────────── ✓ ok · 12.4s ─┐
│ input                                                               │
│ ─────────────────────────────────────────────────────────────────── │
│ command      uv run pytest -x -q tests/                             │
│ description  run the test suite                                     │
│                                                                     │
│ output                                              8,213 lines ─── │
│ ─────────────────────────────────────────────────────────────────── │
│ ........................................................ [  1%]    ▓│
│ ........................................................ [  3%]    ░│
│ ..F..................................................... [  5%]    ░│
│                                                                    ░│
│ ======================== FAILURES ========================         ░│
│ ______ test_workflows_band_lists_running_workflow ________         ░│
│ tests/tui/test_fleet.py:212: assert 4.6 < 4.0                      ░│
└─ ↑↓ PgDn scroll · c copy · f open as file · n/p next call · esc ────┘
```

**One scroll region**, holding the input section and then the output
section. Not two panes: the input is one to four lines that you read
before you start scrolling, a pinned pane would cost those rows on every
call, and two panes raise a question — which one does PgDn move? — that
one region never asks. The command stays legible while you read a failure
because it is also in the title bar.

**Input.** `format_tool_args` for the general case. For `Edit` and
`Write`, the full unified diff via `_render_diff` with no budget: the
diff *is* the call, and an `old_string`/`new_string` dump is a worse
rendering of the same thing.

**Output.** `result.text`, plain, capped at 2,000 rows with a footer
naming how many were dropped. The pane holds the whole string either
way; the cap is about Textual's layout cost, not memory.

**Keys.**

| key | does |
|---|---|
| `escape` | close |
| `↑ ↓ PgUp PgDn Home End` | scroll |
| `c` | copy the whole output to the clipboard, uncapped |
| `f` | write the output to a temp file and open it in a file tab |
| `n` `p` | next / previous tool call in the transcript, in place |

`f` exists because the 2,000-row cap has to have an exit, and
`app._open_file_tab` (`app.py:2180`) is already a scrollable viewer with
search. `n`/`p` exist because scanning a turn's calls one
open-and-close at a time is what would stop anyone using this.

## Where the window gets its data

`_ToolTrack` keeps `result_r`, a rendered Rich object, and throws the
`ToolResult` event away (`pane.py:2646`). So the full text is not
reachable from the live path today, and a replayed block cannot be
clicked at all — `_replay_records` builds its `BlockRecord` without a
`tool_call_id` (`pane.py:1391`), so `CopyableBlock` gets `None` and the
click falls through to copy.

Both are fixed by making the `BlockRecord` the single source, in both
paths:

1. The live tool path mounts with `events=[ev]`, and `_fold_tool_result`
   appends the `ToolResult` to `rec.events` alongside the payload append
   it already does.
2. The replay path passes `tool_call_id=ev.tool_call_id` when the record
   is a `ToolUse`. It already folds the result into `rec.events` via
   `_fold_into`.
3. `_tool_use_idx` (today live-only, `tool_call_id → history index`) is
   populated by the replay path too, from the `use_idx` map it already
   builds.

The modal then resolves `tool_call_id → _history index → BlockRecord`,
and reads `events[0]` (the `ToolUse`) and `events[1]` (the `ToolResult`,
if it landed). A history index is stable: eviction moves `_window_start`
and unmounts widgets (`_evict_top`, `pane.py:1949`) but never drops a
record, so a call from 400 blocks ago still opens.

No new retention. `rec.payload` already carries the full result text for
the clipboard, so keeping the event costs a reference, not a copy.

`n`/`p` walk `_history` for records whose `events[0]` is a `ToolUse`,
from the current index outward.

## Testing

The pure functions carry most of it, and they are the parts that are
wrong in interesting ways:

- `result_digest` per tool: a diff, a 501-line read, zero matches, a
  pytest tail, an error result, an empty result, a result whose only
  content is whitespace.
- `diff_counts` against a pair with a common prefix, so the count is not
  quietly the windowed count.
- `render_tool_use` at a narrow width: the label clips, the elapsed
  column survives, the line is exactly one row. A line that wraps at 60
  columns is the whole feature failing, so it gets an explicit test.

For the modal, Textual's `run_test` pilot: click a tool block and assert
the screen is pushed; `escape` pops it; `n` moves to the next call;
a 50,000-line result mounts 2,000 rows and a footer naming 48,000.

And it is exercised in a real TUI against a daemon started after the
change, per `AGENTS.md` — a click gesture is not proven by a pilot.

## What this does not do

- No search inside the window. `f` hands the output to a file tab, which
  has one.
- No syntax highlighting of the output. A tool result is not reliably any
  one language, and guessing wrong is worse than plain.
- No change to the subagent (`Task`) box, which folds its children flat
  and is a different problem.
