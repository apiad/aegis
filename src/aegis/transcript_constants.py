"""Single source of truth for transcript-windowing tuning knobs, shared by
the TUI pane and (later) the web client's `hello` constants block."""

N_MAX = 300            # max mounted transcript blocks before eviction
EVICT_BATCH = 50       # blocks dropped per eviction when over N_MAX
LOAD_BATCH = 100       # older blocks re-mounted per scroll-up load
STICKY_EPS = 2         # px/row tolerance for "stuck to bottom"
LOAD_MORE_EPS = 3      # scroll-from-top tolerance to trigger load-older
DEBOUNCE_S = 0.15      # debounce window for scroll-up load-older
