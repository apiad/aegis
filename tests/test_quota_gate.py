import pytest

from aegis.quota_gate import decide, pool_for, validate

NOW = 10_000.0


def snap(*windows, read_at=NOW - 30, state="ok", name="claude"):
    return {
        "providers": [
            {"name": name, "state": state, "read_at": read_at, "windows": list(windows)}
        ]
    }


def w(kind, percent, projected=None, resets_at=NOW + 3600, label=None):
    return {
        "kind": kind,
        "label": label or kind,
        "percent": percent,
        "projected": projected,
        "resets_at": resets_at,
    }


LINES = {
    "claude": {
        "mode": "lines",
        "lines": {
            "session": {"launch": 50, "stop": 90},
            "weekly_all": {"launch": "pace", "stop": 90},
        },
    }
}


def test_the_pool_of_a_harness():
    assert pool_for("claude-code", "opus") == "claude"
    assert pool_for("opencode", "opencode-go/kimi") == "opencode-go"
    assert pool_for("opencode", "anthropic/sonnet") is None
    assert pool_for("codex", "gpt") == "codex"


@pytest.mark.parametrize(
    "windows,state",
    [
        ((w("session", 40), w("weekly_all", 30, projected=80)), "open"),
        ((w("session", 50), w("weekly_all", 30, projected=80)), "closed"),
        ((w("session", 40), w("weekly_all", 30, projected=120)), "closed"),
        ((w("session", 90), w("weekly_all", 30, projected=80)), "stopped"),
        ((w("session", 40), w("weekly_all", 95, projected=99)), "stopped"),
    ],
)
def test_lines_decide(windows, state):
    assert decide(LINES, "claude", snap(*windows), NOW).state == state


def test_no_policy_is_never_gated():
    assert decide(None, "claude", snap(w("session", 99)), NOW).state == "open"
    assert decide(None, None, snap(), NOW).state == "open"


def test_a_pool_the_policy_does_not_name_is_closed():
    assert decide(LINES, "opencode-go", snap(w("session", 1)), NOW).state == "closed"
    assert decide(LINES, None, snap(w("session", 1)), NOW).state == "closed"
    assert decide(LINES, "codex", snap(), NOW).state == "closed"


def test_codex_has_no_gauge_and_opens_only_when_named():
    assert decide({"codex": {"mode": "open"}}, "codex", snap(), NOW).state == "open"
    assert decide({"codex": {"mode": "closed"}}, "codex", snap(), NOW).state == "closed"


def test_open_and_closed_modes():
    opened = {"claude": {"mode": "open"}}
    assert decide(opened, "claude", snap(w("session", 99)), NOW).state == "open"
    shut = {"claude": {"mode": "closed"}}
    assert decide(shut, "claude", snap(w("session", 0)), NOW).state == "closed"


def test_a_stale_reading_closes_but_never_stops():
    old = snap(w("session", 99), read_at=NOW - 901, state="stale")
    assert decide(LINES, "claude", old, NOW).state == "closed"
    recent = snap(w("session", 99), read_at=NOW - 600, state="stale")
    assert decide(LINES, "claude", recent, NOW).state == "stopped"
    calm = snap(w("session", 10), read_at=NOW - 600, state="stale")
    assert decide(LINES, "claude", calm, NOW).state == "open"


def test_no_reading_at_all_is_closed():
    assert decide(LINES, "claude", {"providers": []}, NOW).state == "closed"
    failed = snap(read_at=None, state="failed")
    assert decide(LINES, "claude", failed, NOW).state == "closed"


def test_the_why_names_the_window_and_the_line():
    g = decide(
        LINES,
        "claude",
        snap(w("session", 91, label="5 hours"), w("weekly_all", 10, projected=50)),
        NOW,
    )
    assert "5 hours" in g.why and "91" in g.why and "90" in g.why
    assert g.resets_at == NOW + 3600


@pytest.mark.parametrize(
    "policy,ok",
    [
        (LINES, True),
        ({}, True),
        ({"claude": {"mode": "open"}}, True),
        ({"codex": {"mode": "open"}}, True),
        ({"codex": {"mode": "lines", "lines": {}}}, False),
        (
            {
                "claude": {
                    "mode": "lines",
                    "lines": {"session": {"launch": 95, "stop": 90}},
                }
            },
            False,
        ),
        (
            {
                "claude": {
                    "mode": "lines",
                    "lines": {"session": {"launch": "pace", "stop": 101}},
                }
            },
            False,
        ),
        (
            {
                "claude": {
                    "mode": "lines",
                    "lines": {"session": {"launch": True, "stop": 90}},
                }
            },
            False,
        ),
        (
            {
                "claude": {
                    "mode": "lines",
                    "lines": {"rolling": {"launch": 5, "stop": 90}},
                }
            },
            False,
        ),
        ({"claude": {"mode": "maybe"}}, False),
        ({"nope": {"mode": "open"}}, False),
        ([], False),
    ],
)
def test_validate(policy, ok):
    assert (validate(policy) == []) is ok
