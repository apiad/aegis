from aegis.cache import alarm, cold_policy, hit_rate, push
from aegis.claude.stream import Usage

CTX = {"handoff_at": 300000, "cold_after": 3600, "cold_min": 100000}


def u(read=0, write=0, inp=10):
    return Usage(input=inp, cache_creation=write, cache_read=read, output=20)


def test_push_keeps_the_last_ten_and_skips_empty_usage():
    r = []
    for k in range(12):
        r = push(r, u(read=1000), at=float(k))
    assert len(r) == 10 and r[0][2] == 2.0
    assert push(r, u(inp=0), at=99.0) is r


def test_hit_rate_weights_by_tokens():
    r = push(push([], u(read=990), 0.0), u(write=990), 1.0)
    assert hit_rate(r) == 50
    assert hit_rate([]) is None


def test_alarm_only_on_warm_calls_of_a_measured_harness():
    warm = [[100, 1000, 0.0], [100, 1000, 10.0], [100, 1000, 20.0]]
    assert alarm(warm, "claude-code")
    assert not alarm(warm, "opencode")
    cold_then_good = [[0, 1000, 0.0], [0, 1000, 5000.0], [990, 1000, 5010.0]]
    assert not alarm(cold_then_good, "claude-code")
    assert not alarm([[0, 1000, 0.0]], "claude-code")


def test_cold_policy_names_the_thresholds_for_claude_only():
    assert cold_policy("claude-code", CTX) == {"after": 3600, "min": 100000}
    assert cold_policy("codex", CTX) is None
