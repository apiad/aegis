import aegis.transcript_constants as tc
from aegis.tui import pane


def test_canonical_values():
    # 300: briefly 150, reverted. A mounted block costs ~0.033ms of a real
    # layout pass, so halving the window bought ~5ms per frame at the cost
    # of half the instant scrollback. See the constant's own comment for
    # the benchmark artefact that made 150 look worthwhile.
    assert tc.N_MAX == 300
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
