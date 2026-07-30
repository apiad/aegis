import aegis.transcript_constants as tc
from aegis.tui import pane


def test_canonical_values():
    assert tc.N_MAX == 300
    assert tc.EVICT_BATCH == 50
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
