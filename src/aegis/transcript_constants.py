"""Single source of truth for transcript-windowing tuning knobs, shared by
the TUI pane and (later) the web client's `hello` constants block."""

N_MAX = 300            # max mounted transcript blocks before eviction
N_HARD_MAX = 600       # ceiling while scrolled up, where the N_MAX eviction
                       # can't run (see ConversationPane._mount_block)
REPLAY_TAIL = 10       # blocks mounted on resume (rest load on scroll-up)
EVICT_BATCH = 50       # blocks dropped per eviction when over N_MAX
LOAD_BATCH = 40        # older blocks re-mounted per scroll-up load: mounting
                       # is ~3.7ms/block, so 100 was a 370ms hitch per load
STICKY_EPS = 2         # px/row tolerance for "stuck to bottom"
LOAD_MORE_EPS = 3      # scroll-from-top tolerance to trigger load-older
DEBOUNCE_S = 0.15      # debounce window for scroll-up load-older
TOOL_RESULT_HEAD_LINES = 8   # lines of a tool result kept in the compact wire
TOOL_INPUT_HEAD_LINES = 1    # lines of tool input kept in the compact wire
