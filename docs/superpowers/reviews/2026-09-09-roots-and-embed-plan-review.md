# Review: *Aegis Roots and `embed()` Implementation Plan*

*Plan review — 2026-09-09. Target:
`docs/superpowers/plans/2026-09-09-aegis-roots-and-embed.md` (at `ee53558`).
Reviewed against the tree at that commit.*

The evidence base is solid. I re-derived every count and line reference the
plan makes and they all hold: 66 cwd sites across `src/aegis` (20 in `cli.py`,
9 in `tui/app.py`), 26 `find_project_root()` calls in `mcp/server.py`, the
three fallbacks at `core/manager.py:97,117,167`, the two at
`core/session.py:85,91`, `attach_scheduler_context` assigning `state_root` at
`manager.py:136` from its single caller `cli.py:454`, the eight cwd calls in
`_serve` at exactly `cli.py:401,404,405,410,450,455,463,481`, the comment at
`tui/app.py:466`, and the `isolated_project_dir` fixture at `conftest.py:126`.
The signature block in Global Constraints is accurate. The central diagnosis —
`state_root` is `None` on every non-scheduler boot, so the fallback is the
normal path — is correct.

Tasks 1, 2, 3, 6, 8 and 9 are essentially sound and could be executed as
written, modulo the specific corrections below.

**Task 7 cannot be executed as written**, because its central premise about
`AegisApp(manager=…)` is wrong, and both its tests fail for reasons unrelated
to the feature. **Task 4 proposes a large refactor where a 26-line edit would
do**, and the refactor as sketched silently drops two behaviours. Task 5 has a
gap that prevents it going green.

---

## Blocking

### 1. `AegisApp(manager=…)` is a remote-mode flag, not an injection seam

Task 7 Step 3 says:

> `AegisApp` already accepts an externally-built `manager=` — the seam
> `_run_tui_with_manager` (cli.py:295) uses for `--remote`. Reuse it rather
> than adding a second constructor.

`app.py:379` is `if manager is not None:` — and what follows is not dependency
injection. It sets `self._remote_manager = manager`, wires every plane through
`getattr(manager, …, _DisabledPlaneStub(…))`, skips MCP binding, and
`return`s before the entire local-plane construction block. Thirteen sites
then branch on `hasattr(self, "_remote_manager")`, and they turn local
features **off**:

- `app.py:918` — `qm = None if hasattr(self, "_remote_manager") else self.queue_manager`. Queues stop reaching panes.
- `app.py:1318` — `if self._hosts and not hasattr(self, "_remote_manager")`. The execution-hosts axis is disabled.
- `app.py:889, 967, 1215, 1335, 1362` — further local paths short-circuited.

Worse, three of the methods it calls on `_remote_manager` do not exist on
`SessionManager`:

| method | `SessionManager` | `RemoteSessionManager` | called at |
|---|---|---|---|
| `make_pane_core` | ✗ | ✓ | `app.py:1948` |
| `_add_session` | ✗ | ✓ | `app.py:1241` |
| `shutdown` | ✗ | ✓ | `app.py:1665` |

So a `LocalTuiAttachment` built on this seam produces a TUI that paints, then
`AttributeError`s on quit (`shutdown`) and on opening a pane
(`make_pane_core`), with queues and hosts silently dead in between.

Note that **Task 7 Step 7's live launch check would not catch this.** It
starts the app under `timeout 10` and looks for a traceback; mount succeeds,
and the failures are on quit and on interaction. The artifact check needs to
spawn a pane and quit cleanly, not just paint.

The fix is a real seam: a `bridge=` parameter that replaces the local plane
construction *without* setting `_remote_manager`, leaving all thirteen
`hasattr` branches on their local side. That is a genuine change to
`tui/app.py` and should be its own task with its own tests, not a step inside
the boot refactor.

### 2. Both Task 7 tests crash on `mcp=None`

`_serve` calls `mcp.bind(mgr)` and `await mcp.start()` at `cli.py:415-416`,
reads `mcp.port` at `:418`, and `await mcp.stop()` in the `finally` at `:497`.
Passing `mcp=None` raises `AttributeError` before the UI attachment is ever
reached — so `test_local_ui_boot_starts_a_scheduler` and
`test_headless_boot_still_works` fail identically against a correct
implementation and against a broken one. Both need a stub exposing
`bind`, `start`, `stop` and `port`.

(`mcp=None` in the Task 2 manager tests is fine — `SessionManager` only
stores it.)

### 3. Task 4's `build_config_tools` is unnecessary, and lossy as sketched

All 26 `find_project_root()` calls sit inside one function,
`build_server(bridge: AppBridge, tokens=None)` at `mcp/server.py:592`, which
**already holds `bridge`**. After Task 2 gives `SessionManager.roots`, the
whole task is `roots = bridge.roots` at the top of `build_server` plus 26
mechanical line edits. No extraction, no dict of callables, no re-registration.

The proposed factory is a worse shape for three concrete reasons:

**It drops the write lock and the hot-registration.** The real tool
(`server.py:725-755`) is:

```python
    root = find_project_root()
    if root is None:
        return {"error": "no .aegis.yaml found"}
    async with config_write_lock:                       # <- server.py:620
        try:
            _add(root, slug, provider=harness, model=model, ...)
        ...
        agent = Agent(**kw)
        bridge.register_agent(slug, agent)              # <- hot-register
        return {"ok": True, "live": True, "restart_required_for": []}
```

The plan's snippet has neither `config_write_lock` nor `bridge.register_agent`
— it just calls `_edit.add_agent` and returns the success dict. An implementer
copying it ships a tool that persists to disk but no longer hot-registers (the
docstring's entire promise), and races the other twelve config writers.
`config_write_lock` and `bridge` are both closure state of `build_server`;
extracting the tools means threading both out.

**The tools are registered by decorator.** `@server.tool` above each `async
def`, and FastMCP reads the function's signature, annotations and docstring.
Returning a `dict[str, object]` and "wiring the registration site" means
re-registering each one — the plan gives no detail, and the docstrings (which
are the agent-facing tool descriptions) are exactly what an ellipsis-bodied
snippet loses.

**The `find_project_root(` source guard doesn't match the factory's scope.**
Two of the 26 (lines 1625, 1639) are in `aegis_run_dynamic_workflow`, not a
config tool. The guard demands zero occurrences file-wide; nothing named
`build_config_tools` covers that one.

Separately, and true under *either* approach: **thirteen tools return
`{"error": "no .aegis.yaml found"}` when the root is `None`.**
`roots.config_root` is never `None`, so that documented branch disappears for
all of them — an embedded instance rooted at a directory without `.aegis.yaml`
now writes one instead of erroring. The plan flags return-shape stability for
`aegis_config_show` only; the decision needs to be made once, for all
thirteen, and stated.

---

## Should fix

### 4. Task 5's guard cannot go green

`CLEANED` includes `mcp/server.py`, which holds two `Path.cwd()` sites that are
**not** `find_project_root()` calls and that no task removes:

- `server.py:616` — the `CommsLedger` state-dir fallback when the bridge has no queue manager.
- `server.py:2344` — `_register_user_tool`'s `state_dir = Path.cwd() / ".aegis" / "state"`.

Step 2's expected-failure list also omits them. Either add both to Task 4's
scope (the second one needs the root threaded into `_register_user_tool`,
which is called from `build_server`, so it is reachable) or drop
`mcp/server.py` from `CLEANED` and say why.

### 5. Task 7 Step 5 deletes two things that are not config loading

`cli.py:163-214` is not all boot config. Two behaviours live inside it:

**Bootstrap mode** (`163-168`). A missing `.aegis.yaml` currently yields
`agents = {}`, `default_agent = ""`, and drops into the TUI ConfigPanel — the
documented "no config anywhere → don't refuse" path. `load_boot_config`
(Task 8) raises `ConfigError`, so `aegis` in a fresh directory would exit 1.
That is a user-visible regression, and it violates the plan's own Global
Constraint that stages 1–2 change no behaviour but the schedule fix.

**Workspace resume** (`198-205`):

```python
    try:
        pick_workspace_to_resume(state_dir(Path.cwd()), clean=clean)
    except CorruptWorkspace as e:
        ...
        raise typer.Exit(code=2)
```

`_run_serve` has no equivalent, so deleting the range drops resume-state
selection and the exit-code-2 contract. It is also itself a cwd site that must
become `roots.state_dir` — and the natural home for it is inside
`LocalTuiAttachment.run`, since it is UI state, not brain state.

### 6. `BootConfig` omits `web` (and the prompt-command load)

`_run_serve` computes at `cli.py:737` `web = yaml_cfg.web if (yaml_cfg.web and
yaml_cfg.web.token) else None` and passes it to `_serve`, which uses it to
start `WebFrontend` (`cli.py:479-483`). Task 8's `BootConfig` has no `web`
field, so replacing the guard block as specified silently removes the web UI
from `aegis serve` — in a plan whose stated contract is *"the web client is
untouched and keeps working."*

The same block also calls `load_prompt_commands(root)` (`cli.py:732`), a side
effect with no home in the new `BootConfig`.

### 7. `LocalTuiAttachment` degrades the local TUI's agent map and hosts

The current local construction (`cli.py:226-230`) passes `agents` (real
`Agent` objects), `hosts=hosts` and `host_registry=host_registry`. The plan's
attachment passes neither hosts value, and builds `agents = {slug: None for
slug in manager.list_agents()}` — the *remote* idiom from
`_run_tui_with_manager:302`, where real `Agent` objects genuinely aren't
available.

Locally they are, and they are used:

- `app.py:642` — `agent = self._agents[tab.profile]` → `drv.resume(agent, …)` (workspace resume).
- `app.py:824` — `self._agents[slug]` → `self._resolve_place(agent, host, cwd)`.
- `app.py:1120` — `self._agents.get(self._default_agent)`.
- `app.py:2354, 2402` — `_overlay_agent(self._app._agents[slug], model=…)` (per-session model override).

`None` breaks all four.

---

## Minor

**8. `manager.py:167` is a config root, not a state root.** `reload_plugins`
does `root = self.state_root or Path.cwd()` and then
`yaml_loader.load_config(root)`. Task 2 Step 4 replaces it with
`self.state_root`. It should be `self.roots.config_root` — this is precisely
the site where the two roots are allowed to differ, and using `state_root`
preserves the conflation the plan exists to remove. (Invisible under
`for_project`, which is what makes it worth writing down.)

**9. `local_root` still exists on `SessionManager`.** Task 6 removes it from
`_serve` but `SessionManager.__init__` keeps it (`manager.py:58`,
`self._local_root` at `:67`) and `_serve` passes it at
`cli.py:385`. Task 6 should say `local_root=str(roots.harness_cwd)` at that
call, or Task 2 should drop the parameter.

**10. `@pytest.mark.asyncio` contradicts the plan's own constraint.** Global
Constraints line 24 says new tests omit it under `asyncio_mode = "auto"`;
Tasks 4, 7 and 9 all use it. Harmless, but pick one.

**11. `test_no_find_project_root_calls_remain_in_mcp_server` is a substring
assertion.** It pins the text, not the behaviour — it would pass with the call
split across a line break, and fail on the word appearing in a comment
explaining why it was removed. Given the workspace's standing preference for
asserting on the substrate, `ast.walk` over the parsed module looking for a
`Call` to that name is barely longer and cannot be fooled either way.

---

## What the plan gets right, and should keep

- **The gate is the right gate.** Asserting on `.aegis.yaml` *contents* rather
  than path disjointness is exactly the distinction that matters, and the
  reasoning for it (paths diverge as soon as roots are threaded, while the
  late-bound lookups still read the process cwd) is correct.
- **Task 9 Step 6's mutation check.** Deliberately reverting one lookup and
  requiring red is the step that makes the gate worth having.
- **Task 1 Step 5**, pinning `AegisRoots.state_dir` against
  `state.workspace.state_dir` (`workspace.py:67`), catches a real class of
  silent split.
- **Task 2 Step 5**, raising rather than reassigning in
  `attach_scheduler_context`, is the right shape — a silent disagreement there
  is how the bug got in.
- **Task 8 Step 5**, reading the rc directly and not through a pipe, is
  correct and the sort of thing that usually gets skipped.

## Suggested resequencing

Task 7 is doing two things: threading a UI attachment through `_serve`, and
teaching `AegisApp` to accept an injected local bridge. The second is the
risky half and belongs on its own:

- **7a — `AegisApp(bridge=…)`**: a local injection path distinct from
  `_remote_manager`, with tests that spawn a pane, exercise queues and hosts,
  and quit cleanly. No `cli.py` changes.
- **7b — `_serve(ui=…)`**: the attachment protocol and routing `aegis`
  through it, on top of 7a. Bootstrap mode and workspace resume land here,
  in the attachment.

That also lets 7a be reverted independently if the injection turns out to need
more of `tui/app.py` than expected, without unwinding the boot unification.
