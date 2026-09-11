# know-how: embedding aegis as a library

**Reach for this when** you drive aegis from another program
(`aegis.embed()`) instead of from a terminal, or when you touch anything
that resolves a path — the three roots replaced `Path.cwd()` and must stay
threaded.

## The three roots

`src/aegis/config/roots.py` holds the whole of it: one frozen dataclass,
`AegisRoots`, naming three directories that a CLI conflates and an embedded
instance does not.

| Root | Anchors |
|---|---|
| `config_root` | `.aegis.yaml`, its `.aegis/{agents,queues,schedules,hosts,groups}/*.yaml` overlays, and every `plugin_dirs:` entry (resolved as `root / <entry>` in `yaml_loader.load_config`) |
| `state_root` | the parent of `.aegis/state` — transcripts, locks, canvas, terminals, queues, schedules. `roots.state_dir` is the derived `state_root / ".aegis" / "state"` and mirrors `state.workspace.state_dir` so the two never drift |
| `harness_cwd` | the directory an agent subprocess actually runs in — what `LocalLauncher`/`SshLauncher` pass as `cwd`, and what `HostRegistry(local_root=…)` is built from |

`AegisRoots.for_project(root, harness_cwd=None)` sets all three from one
directory. That is the CLI case, and it is why the three were a single
`Path.cwd()` for so long: on a terminal they genuinely are the same
directory, so every `or Path.cwd()` fallback looked correct. They are not
the same directory once one process holds several instances, each rooted at
a different worktree — which is the whole reason the value object exists.

`load_boot_config(roots)` (`cli.py:98`) is the one config load every entry
point goes through, and it reads `roots.config_root` and nothing else. It
raises `ConfigError` rather than calling `typer.Exit` — a library caller
must see the exception instead of having the process exited out from under
it.

One asymmetry worth knowing before you rely on it: a relative `prompt:`
persona path resolves under the launcher's `local_root`
(`hosts/launcher.py:46`), which `embed()` builds from `harness_cwd`, not
from `config_root`. Identical on a CLI; different the moment you pass an
explicit `harness_cwd=`.

## `embed()` owns neither the loop nor the signals

```python
async with aegis.embed("/path/to/project") as ae:
    ae.manager        # the SessionManager — the AppBridge
    ae.queues         # its QueueManager
    ae.roots          # the AegisRoots it booted against
    ae.mcp            # its AegisMCP; .mcp.server is the FastMCP instance
```

`embed()` is an async context manager that runs inside **your** event loop.
It never calls `asyncio.run`, and it installs no signal handlers — the host
owns both. `tests/test_multi_instance.py::test_embed_installs_no_signal_handlers`
pins the signal half.

aegis itself is signal-clean (the handlers the CLI wants live in
`_run_serve`), but the MCP plane runs on uvicorn, and `Server.serve()`
enters `capture_signals()` unconditionally on the main thread
(`uvicorn/server.py:78` → `:322`, which `signal.signal`s SIGINT and SIGTERM
at `:329`; verified against uvicorn 0.44.0). There is no config flag to opt
out. So `_keep_host_signals` saves both handlers around `mcp.start()` and
puts them straight back.

**Why putting them back mid-flight is safe:** uvicorn restores whatever it
captured when `serve()` unwinds, and what it captured *is* the host's
handlers — it read them before we wrote them back. Both sides therefore
restore to the same value; nothing is left dangling when the plane stops.
A handler that reads back as `None` was installed from outside Python and
cannot be restored, so it is left alone rather than guessed at.

## One MCP plane, bound once, started once

`_serve` is the single boot path, and the only thing that varies is the UI
attachment (`UIAttachment`, `cli.py:312` — one `async def run(self, manager)`).
`_serve` wires every subsystem, calls `mcp.bind(mgr)`, and then:

- **no attachment** (`ui is None`) → it starts the plane itself;
- **an attachment** → it defers `start()` to the attachment.

The deferral is not tidiness. `AegisMCP.bind()` only records the bridge;
`build_server` captures it later, *inside* `start()` (`mcp/runtime.py:68-75`).
A front end is the AppBridge the plane has to address — its panes *are* the
sessions an agent calling `aegis_list_sessions` means — so it rebinds this
same object to itself and starts it. Starting in `_serve` instead would
freeze the plane onto a manager that owns no panes, and the later rebind
would be silently ignored.

`embed()`'s `_Capture` attachment is the other half of that rule. There is
no app, so the manager already *is* the right bridge and nothing rebinds —
which means the attachment owes the plane its `start()`, and it does it
inside `_keep_host_signals`. It publishes the manager only after the server
is up, so a caller holding an `EmbeddedAegis` can reach `.mcp.server`
without racing it.

Do not construct a second `AegisMCP` anywhere in this path. It picks its
port in `__init__`, so you would get a working server on a second port
addressing a bridge with no sessions on it.

## The multi-instance contract

Several embedded instances coexist in one process. Each gets its own
`AegisRoots`, `SessionManager`, `QueueManager`, `HostRegistry` and
`AegisMCP` (own port), and they share nothing.

The property that matters is not that their paths differ — paths differ as
soon as roots are threaded at all — but that a *write* through one
instance's MCP surface lands only in that instance.
`tests/test_multi_instance.py` is the gate, and it asserts on exactly that:
it calls `aegis_config_add_agent` on instance A's real server and checks
B's `.aegis.yaml` is byte-identical afterwards. It stands the process cwd
inside project **B** on purpose. Without that, a tool that resolved its
root by walking up from the cwd would land in the pytest tmp dir — neither
project — and B's file would stay unchanged for the wrong reason.

## The trap: `Path.cwd()` inside an embedded instance is a bug

The process cwd belongs to the **host**, not to any instance. An embedded
aegis may be rooted anywhere, and two of them are rooted in two different
places while the process stands in a third. Any `Path.cwd()`,
`os.getcwd()`, or bare `find_project_root()` reached from instance code
therefore resolves against a directory that has nothing to do with the
instance doing the resolving — and it fails *silently*, by writing to a
plausible wrong file rather than raising.

`tests/test_no_cwd_regression.py` pins the modules already cleaned:

    cli.py::_serve, core/manager.py, core/session.py, mcp/server.py,
    terminal/manager.py, workflow/engine.py, usage/env.py

It matches **by AST, not by substring** — a text scan is fooled by
`Path.cwd(\n)` (verified by mutation) and fires on a mention inside a
comment, both of which bit the substring version on the day it was written.
It also supports a `module::function` form, used for `cli.py::_serve`,
because `cli.py`'s typer entrypoints legitimately read the process cwd:
that is the CLI's *input*. Only the boot path is cleaned.

When you clean another module, add it to `CLEANED`. When you add code to
one that is already listed, thread the root in — `self.roots.config_root`,
`roots.state_dir`, `self.project_root` — rather than reaching for the
process.
