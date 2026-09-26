"""Which of the brain's planes a view renders, declared once.

`AegisApp` builds every plane in its constructor because it IS the
AppBridge in the interactive path. Under the daemon the brain is the
bridge, and building them again gives two of everything: agents reach the
brain's through MCP, the UI renders the view's, and they never meet. An
agent arms a monitor, the tool succeeds, and the strip stays empty.

The spec settles which is which. `2026-09-07-retire-web-ui-tui-over-web-design.md`,
*Brain state versus view state*: "queues, monitors, canvas, terminals,
hosts" are brain state, one copy, all views.

This module is data. It imports nothing from `aegis.tui` so that the
coverage test can hold both sides at once without dragging Textual in.
"""

from __future__ import annotations

#: Manager attributes a bridged view adopts rather than constructs. Each
#: name N has a matching ``SessionManager.attach_N``; the coverage test
#: asserts that, because the adoption code derives one from the other.
BRAIN_PLANES: tuple[str, ...] = (
    "queue_manager",
    "monitor_manager",
    "reminder_service",
    "canvas_manager",
    "terminal_manager",
)

#: Brain state a view adopts that has no ``attach_*``: the manager builds
#: it in its own constructor, so it is named here rather than derived.
#: Every one is keyed by handle, so two copies means a message, claim or
#: group delivered to one is invisible to the other.
CONSTRUCTED_PLANES: tuple[str, ...] = (
    "inbox_router",
    "locks",
    "groups",
    "loop_service",
)

#: `attach_*` methods that are deliberately not rendered by a view, and
#: why. Listed so that a new one cannot be added without somebody deciding
#: which side it belongs on.
NOT_VIEW_FACING: dict[str, str] = {
    "attach_persistence": (
        "a state directory, not an object; the view reads the same "
        "directory through its own roots"
    ),
    "attach_locks_state": (
        "rebuilds the brain's `locks` with persistence; the view adopts "
        "that object through CONSTRUCTED_PLANES"
    ),
    "attach_remotes": ("peer configuration consumed by the remote plane, not rendered"),
    "attach_remote_plane": ("the inbound HTTP plane; has no UI surface"),
    "attach_scheduler_context": (
        "wiring for the scheduler's own loop; the view shows schedules "
        "through MCP tools, not through this object"
    ),
}

#: Planes whose constructor takes a `state_dir` and which persist nothing
#: without one. Each pair is (constructor name, module path relative to the
#: aegis package) of a BRAIN path that must hand it a directory.
#:
#: QueueManager and InboxRouter were built without it in both brain paths
#: from the day persistence landed, so the queue never wrote a record and
#: its replay never ran. Nothing said so, because every test that
#: covered the replay constructed the manager itself.
#:
#: `ParkReaper` has the same two-brain-path exposure and is not caught by
#: anything here. It was REACHABLE, not theoretical: `cli.py` armed it and
#: `tui/app.py` did not, and a standalone TUI does not need to be launched
#: with queues to have one — `/queues new` hot-registers a queue into the
#: running app's `QueueManager` through `AegisApp.register_queue`. So a
#: worker could park in a standalone TUI and `recoverable_ttl_s` would
#: never be checked: the parked session stood for the life of the process.
#: `AegisApp.on_mount` arms the reaper on the unbridged path now, but
#: nothing structural catches "this plane needs a driver on a clock in
#: every brain path" — this list catches the `state_dir` half of the same
#: class of bug and no more.
STATEFUL_PLANES: tuple[tuple[str, str], ...] = (
    ("QueueManager", "cli.py"),
    ("InboxRouter", "cli.py"),
    ("QueueManager", "tui/app.py"),
    ("InboxRouter", "tui/app.py"),
)
