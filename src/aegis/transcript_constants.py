"""Single source of truth for transcript-windowing tuning knobs, shared by
the TUI pane and (later) the web client's `hello` constants block."""

N_MAX = 300            # max mounted transcript blocks before eviction
REPLAY_TAIL = 10       # blocks mounted on resume (rest load on scroll-up)
EVICT_BATCH = 50       # blocks dropped per eviction when over N_MAX
LOAD_BATCH = 100       # older blocks re-mounted per scroll-up load
STICKY_EPS = 2         # px/row tolerance for "stuck to bottom"
LOAD_MORE_EPS = 3      # scroll-from-top tolerance to trigger load-older
DEBOUNCE_S = 0.15      # debounce window for scroll-up load-older
TOOL_RESULT_HEAD_LINES = 8   # lines of a tool result kept in the compact wire
TOOL_INPUT_HEAD_LINES = 1    # lines of tool input kept in the compact wire
