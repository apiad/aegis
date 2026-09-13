"""How busy the host was, from two /proc/stat readings.

Measured 2026-09-13: at load average 7.2 on 8 cores the CPU was 17% busy
during the window, and the block stream measured its quiet-host latency.
The load average overstates contention, so the busy share is the signal.
"""
from aegis.bench.host import busy_pct


def test_busy_share_between_two_readings():
    # 400 jiffies passed, 100 of them idle: 75% busy.
    assert busy_pct((1000, 2000), (1100, 2400)) == 75.0


def test_an_idle_window_is_zero_percent():
    assert busy_pct((1000, 2000), (1400, 2400)) == 0.0


def test_no_elapsed_jiffies_has_no_answer():
    assert busy_pct((1000, 2000), (1000, 2000)) is None
    assert busy_pct((0, 0), (0, 0)) is None
