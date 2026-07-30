"""Single source of truth for transcript-windowing tuning knobs, shared by
the TUI pane and (later) the web client's `hello` constants block."""

N_MAX = 300            # max mounted transcript blocks before eviction.
                       # Briefly 150, on a measurement that turned out to be
                       # an artefact: the benchmark had `await pilot.pause()`
                       # inside the timed region, and Pilot._wait_for_screen
                       # posts a callback to EVERY mounted widget and awaits
                       # them (textual/pilot.py:490), so the harness cost
                       # scaled with the variable under test. A mounted block
                       # really costs ~0.033ms of a real layout pass, not
                       # 0.21ms, so halving the window bought ~5ms per frame
                       # and cost half the instant scrollback. Not a trade
                       # worth making.
REPLAY_TAIL = 10       # blocks mounted on resume (rest load on scroll-up)
EVICT_BATCH = 50       # blocks dropped per eviction when over N_MAX
LOAD_BATCH = 40        # older blocks re-mounted per scroll-up load: mounting
                       # is ~3.7ms/block, so 100 was a 370ms hitch per load
STREAM_REPAINT_S = 0.05  # min gap between streaming repaints (~20/s). Each
                       # repaint is a refresh(layout=True), i.e. a full
                       # compositor rebuild costing ~0.33ms per mounted block
STICKY_EPS = 2         # px/row tolerance for "stuck to bottom"
LOAD_MORE_EPS = 3      # scroll-from-top tolerance to trigger load-older
DEBOUNCE_S = 0.15      # debounce window for scroll-up load-older
TOOL_RESULT_HEAD_LINES = 8   # lines of a tool result kept in the compact wire
TOOL_INPUT_HEAD_LINES = 1    # lines of tool input kept in the compact wire
