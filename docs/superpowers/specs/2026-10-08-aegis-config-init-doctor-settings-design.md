# aegis: creating, checking and editing `.aegis.yaml`

**Status: designed, 2026-10-08** (issue #193). Not built. Designed with Alex in
a brainstorm in chat; one PR carries all of it.

## What this delivers

A person starting aegis in a new directory runs `aegis init`, answers a few
prompts whose answers are already filled in, and gets a `.aegis.yaml` with one
agent per harness installed on the machine, a default agent and one queue.
`aegis doctor` checks that file at any time and names every problem by its place
in the file. In the browser, a Settings page edits the same file through a form,
and a button on it runs the doctor and marks each finding on its row.

aegis re-reads `.aegis.yaml` whenever it changes on disk, whoever changed it. A
save from the Settings page only writes the file; the running server picks the
change up the same way it picks up an edit made in a text editor.

## What is wrong today

- `queues.py` reads `queues:` once, in `Queues.__init__`, so a queue added or
  changed in the file does nothing until the server restarts. Agents are re-read
  on every `agents.list` and `session.spawn`, so the two halves of the file
  follow different rules.
- A parse error makes `agents.list` and `session.spawn` fail with `bad_config`,
  and makes `load_queues` return no queues at all, silently. One stray character
  typed in an editor stops every spawn and every queue until it is fixed.
- aegis reads three top-level keys (`agents`, `queues`, `default_agent`) and
  ignores every other key without a word. `scheduler:` and `voice:` sat in
  configs doing nothing (#109, #110).
- `aegis serve` in a directory with no config starts, and the first spawn fails
  with "no agent given and no default_agent in .aegis.yaml". Nothing tells the
  person what to write, which harnesses they have, or which models those offer.
- Nothing checks that an agent's harness is installed, that its model exists in
  that harness, or that its effort is one the model takes, until a spawn fails.

## Decisions

| Question | Decision | Why |
|---|---|---|
| Where config lives | `.aegis.yaml` only. The server holds a parsed copy and nothing else | A Settings save that also patched in-memory state would make two paths to the same change, and an editor edit would take only one of them |
| How a change is noticed | The server `stat`s the file once a second and re-reads it when its `(mtime_ns, size, inode)` changes | Polling one `stat` costs nothing. inotify misses an editor's write-then-rename unless it watches the directory, and has made the test suite flaky before |
| A file that does not parse | The last config that parsed stays in force, and the error is published | A half-typed edit must not stop every spawn. The error is shown on the Settings page and by the doctor, so it is never silent |
| Who writes the file | `aegis init` and the Settings page, both through one writer | One writer keeps the rules (round-trip YAML, validate first, refuse a stale write) in one place |
| Comments and unknown keys | Kept. The writer uses ruamel's round-trip mode and edits the document in place | Alex's config carries comments. A writer that drops them would make the page unusable on a hand-kept file |
| Defaults | Still none in the loader. `init` writes every value into the file | DESIGN.md: a setting the loader filled in is one nobody chose. A value `init` proposed and the person accepted is in the file, where anyone can see it |
| Raw YAML on the page | No. The form only | The person's editor already edits the raw file, and the stale-write check keeps the page and the editor from overwriting each other |
| Doctor for agents | `config.doctor` is an agent operation; `config.write` is a person's | The doctor only reads. An agent that rewrote the config could raise its own permission |

## The config holder

A new module, `config.py`, owns the file. `agents.py` keeps the agent rules
(`_agent`, `resolve`, `EFFORTS`, `PERMISSION_ORDER`); `queues.py` keeps
`load_queues`'s rules as a function over a mapping. `read_config`,
`load_agents`, `default_agent` and the per-call file reads go.

```python
@dataclass(frozen=True)
class Snapshot:
    path: Path                # <config_root>/.aegis.yaml
    exists: bool
    stamp: tuple | None       # (mtime_ns, size, inode); None when absent
    agents: tuple[Agent, ...]
    default_agent: str | None
    queues: dict[str, dict]   # load_queues' shape, errors included
    unknown_keys: tuple[str, ...]
    error: str | None         # the parse error of the file on disk, if any

class Config:
    def __init__(self, config_root: Path, on_change: Callable[[Snapshot], None]): ...
    def current(self) -> Snapshot      # stat; re-read if the stamp moved
    async def watch(self) -> None      # current() every WATCH_EVERY_S (1.0)
```

`current()` is what every reader calls: `agents.list`, `session.spawn`,
`Queues.dispatch` and `_start`, the `/model` suggestions. It costs one `stat`
when nothing changed. When the stamp moved it re-reads the file:

- the file parses: a new snapshot replaces the old, with `error=None`;
- it does not parse: the old snapshot's agents, default and queues stay, the
  stamp moves (so the same broken file is not re-parsed every second), and
  `error` carries the message;
- it was deleted: the snapshot is empty, `exists=False`. Deleting the file is a
  choice the person made, so it is honoured.

On every change `on_change` fires. The app publishes the snapshot on a `config`
channel and calls `Queues.dispatch()`, which already fails pending tasks whose
queue was removed or broken and starts tasks a raised `max_parallel` now allows.
A lowered `max_parallel` stops nothing that is running; new tasks wait until the
running count drops below it.

The watch loop runs from `App.boot` and stops in `App.shutdown`, next to the
quota and host samplers. Reads between ticks call `current()` themselves, so a
spawn right after a save never sees the old file, whatever the tick.

## The checks (`doctor.py`)

```python
@dataclass(frozen=True)
class Finding:
    level: Literal["ok", "warn", "error"]
    where: str     # "file", "agents.deepseek.model", "queues.general", "harness.opencode", "state"
    message: str

async def doctor(roots: Roots, bins: dict[str, str]) -> list[Finding]
```

`bins` maps a harness to its binary, as `aegis serve --claude/--opencode` does.
The checks, in this order:

1. **The file.** Where it is, and which directory `aegis` walked up from to find
   it. Missing is an error, with "run `aegis init`". A parse error is an error
   with ruamel's line and column.
2. **Unknown keys.** Each top-level key other than `agents`, `queues` and
   `default_agent` is a warning: "aegis does not read `scheduler`". Unknown keys
   inside an agent (other than `harness`, `provider`, `model`, `effort`,
   `permission`, `priming`) and inside a queue are warnings too, which catches a
   misspelt `efort`.
3. **Harnesses.** For each harness an agent names: the binary is on `PATH`
   (error if not) and answers `--version` within 5 s (its version is the `ok`
   message). Then the harness is probed for its catalog (`Harness.probe`, about
   0.5 s for Claude and 3 s for OpenCode, no tokens). A probe that fails is an
   error: for Claude it usually means not logged in, and the message carries the
   probe's stderr tail.
4. **Agents.** Each agent's own `error` from `_agent` is an error. Its model not
   in the harness's catalog is a warning, not an error, because a CLI alias can
   resolve without being listed. Its effort not in the model's `efforts` is a
   warning when the catalog lists efforts for that model.
5. **The default.** `default_agent` missing, naming no agent, or naming one with
   an error, is an error, because a spawn with no agent named fails.
6. **Queues.** A queue's own `error` is an error; an `agent` naming no agent, or
   one with an error, is an error.
7. **State.** The state directory is creatable and writable; legacy marker files
   in it are an error, with the move `aegis serve` already asks for.

Probes run concurrently per harness, so a full doctor on this machine takes
about as long as the slowest probe. The doctor never writes anything.

## Detection (`detect()`, in `doctor.py`)

```python
@dataclass(frozen=True)
class Found:
    harness: str            # "claude-code" | "opencode"
    bin: str | None         # absolute path, or None when not on PATH
    version: str | None
    models: tuple[Model, ...]   # from the probe; empty when it failed
    error: str | None

async def detect(cwd: Path, bins: dict[str, str]) -> list[Found]
```

Only harnesses aegis can run are looked for (`agents.HARNESSES`). The doctor's
harness checks and `init` both use it.

## `aegis init`

```
aegis init [--root DIR] [--yes] [--claude BIN] [--opencode BIN]
```

1. Refuses if `<root>/.aegis.yaml` exists: "already configured; run `aegis
   doctor`". `--root` defaults to the current directory, not the walked-up root,
   because init creates a root.
2. If a parent directory has a config, says which one governs this directory now
   and asks whether to create a new root here (`--yes` answers yes).
3. Runs `detect()` and prints each harness, its version and how many models it
   offers. With none installed it stops and says how to install Claude Code.
4. Proposes, and asks for each value with the proposal pre-filled (Enter
   accepts; `--yes` accepts all):
   - for Claude Code: an agent `opus`, model `opus`, effort `high`, permission
     `full`;
   - for OpenCode: an agent named after the model's last path segment, with the
     first model in its catalog, effort the model's first listed effort or
     `high`, permission `full`;
   - `default_agent`: the Claude agent if there is one, else the first;
   - a queue `general` on the default agent, `max_parallel: 3`.
5. Writes the file through the writer, prints it, and runs the doctor.

`init`'s prompts are plain `typer.prompt` and `typer.confirm`, so `--yes` and a
piped stdin both work in tests.

## The writer (`config.write`)

```python
def write(config_root: Path, doc: ConfigDoc, expected_stamp: tuple | None) -> Snapshot
```

`ConfigDoc` is a pydantic model of what the form edits: an ordered list of
agents (name, harness, model, effort, permission, priming), `default_agent`, and
an ordered list of queues (name, agent, max_parallel).

1. **Stale check.** If the file's stamp is not `expected_stamp`, refuse with
   `stale`: "the file changed on disk since you opened it". `None` means the
   caller expects no file.
2. **Validate.** Build the snapshot the new document would produce. Any agent or
   queue error, a duplicate name, or a `default_agent` naming no agent refuses
   the write with every problem listed by `where`. Doctor warnings do not block
   a write: a model the catalog does not list may still be right.
3. **Edit in place.** Load the file in ruamel round-trip mode and change only
   what differs: update changed fields, add new agents and queues at the end,
   delete removed ones. An agent written as `provider: claude-code` keeps that
   form; a nested `provider:` mapping has its `name` updated; a new agent uses
   `harness:`. Comments, key order and keys aegis does not read stay.
4. **Write then rename** into place, and return `Config.current()`, which picks
   the change up at once rather than at the next tick.

## Operations

Registered on the one registry, so the page and agents call the same code.

| Operation | Who | What |
|---|---|---|
| `config.read` | person | The snapshot plus the `ConfigDoc` the form edits and its `stamp`, the path, and `exists` |
| `config.write` | person | `{doc, stamp}`; the writer above |
| `config.detect` | person | `detect()`, cached for 60 s so opening the page twice costs one probe |
| `config.doctor` | person and agent | `doctor()`; findings as data |
| `config.propose` | person | The document `init` would propose, for an empty workspace's Set up |

A `config` channel carries the snapshot: a `set` patch on every change, so an
open Settings page and the new-tab composer's agent list follow edits made
anywhere.

## The Settings page

`#settings`, opened by a gear button in the header next to `?`, and by `Alt+,`
in the key table.

- **Header.** The file's path and, if the file on disk does not parse, the error
  in a red band: "aegis is still using the last version that parsed".
- **Agents.** One row per agent: name, harness (select from `HARNESSES`, with
  ones `detect` did not find marked "not installed"), model (an input with a
  datalist from that harness's detected models), effort, permission, and a
  folded priming textarea. Add and delete buttons.
- **Default agent.** A select of the agents.
- **Queues.** One row per queue: name, agent (select), max parallel. Add and
  delete.
- **Save** sends `config.write` with the stamp the form was loaded with. A
  `stale` answer offers "Reload from disk", which discards the form's edits. A
  validation refusal marks each row it names.
- **Run doctor** shows the findings above the form and marks each on the row its
  `where` names; findings with no row (file, harness, state) stay in the list.
- **Live.** A `config` patch while the form has no unsaved edits reloads it. With
  unsaved edits it shows "the file changed on disk" with Reload, and leaves the
  form alone.
- **Empty workspace.** With no file: "No `.aegis.yaml` at `<root>`." and a
  **Set up** button that fills the form from `config.propose`. Saving creates the
  file.

The page follows the client's rules: a renderer is a function returning a Node,
every decision (which row a finding marks, which harness is installed) arrives
as data from Python, and the keys go in `keys.js`.

## Testing

- `test_config.py`: a change on disk is seen by `current()` with no tick; a
  parse error keeps the last good snapshot and sets `error`; delete empties it;
  the watch loop fires `on_change` once per change, not once per tick.
- `test_config_write.py`: comments and unknown keys survive a write (a fixture
  copied from the Workspace's real `.aegis.yaml`); each `provider:` form is
  kept; a stale stamp is refused; an invalid document writes nothing (compare
  the file's bytes before and after).
- `test_doctor.py`: each check, against fake harness binaries on a temp `PATH`
  (`tests/fake_claude.py` and `tests/fake_opencode.py` already answer the
  catalog probe; both gain a `--version` flag, which neither answers today); a
  missing binary; an unknown top-level key; a model missing
  from the catalog is a warning, not an error.
- `test_cli_init.py`: `--yes` in an empty directory with both fakes on `PATH`
  writes a file the doctor passes; refusal when a file exists; the parent-config
  question.
- Queues: a queue added to the file on disk takes an enqueue with no restart; a
  raised `max_parallel` starts a waiting task.
- Browser (`test_browser.py`): open Settings, change an agent's effort, Save, and
  the file on disk changed and the composer shows the new effort; edit the file
  on disk and the open page reloads; Run doctor marks a row; an empty root shows
  Set up and saving creates the file.
- Live: `aegis init --yes` and `aegis doctor` against the real `claude` and
  `opencode` on zion, in a scratch directory.

## Out of scope

- Editing anything aegis does not read. Unknown keys are kept and reported, never
  edited.
- A raw YAML editor on the page.
- `aegis doctor --fix`. The doctor names the fix in its message; the page or the
  editor applies it.
- Harnesses aegis cannot run (Gemini, Copilot). Detection does not look for them.
- Checking the stored transcripts, which was the legacy `doctor`'s job.
