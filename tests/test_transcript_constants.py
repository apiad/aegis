import aegis.transcript_constants as tc
from aegis.tui import pane


def test_canonical_values():
    # 150, not 300: a mounted block costs ~0.21ms on every reflow and
    # ~6.8ms to re-mount once, so a smaller window trades occasional
    # scroll-up latency for continuous keystroke latency. Still ~7.5
    # viewports of instant scrollback. The web client reads this too.
    assert tc.N_MAX == 150
    assert tc.EVICT_BATCH == 50
    # ~20 repaints/second while streaming. Each is a full compositor
    # rebuild; Textual's own Markdown docs put the ceiling in the same place.
    assert tc.STREAM_REPAINT_S == 0.05
    # 40, not 100: mounting is ~3.7ms/block, so a scroll-up load
    # was a 370ms hitch. The web client reads this same value.
    assert tc.LOAD_BATCH == 40
    assert tc.STICKY_EPS == 2
    assert tc.LOAD_MORE_EPS == 3
    assert tc.DEBOUNCE_S == 0.15


def test_pane_reexports_same_objects():
    # pane keeps exposing the names so existing references resolve, and
    # they are the very same objects (single source of truth).
    assert pane.N_MAX is tc.N_MAX
    assert pane.EVICT_BATCH is tc.EVICT_BATCH
    assert pane.STICKY_EPS is tc.STICKY_EPS
    assert pane.DEBOUNCE_S is tc.DEBOUNCE_S
