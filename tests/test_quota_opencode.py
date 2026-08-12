import json
import urllib.error
from datetime import timezone

import pytest

from aegis.usage.quota import QuotaError
from aegis.usage.quota_opencode import fetch_usage, parse_usage, read_key

# Captured verbatim from a live GET against the endpoint.
LIVE = {
    "usage": {
        "rolling": {"status": "ok", "percent": 0,
                    "resetsAt": "2026-08-13T01:58:33.681Z"},
        "weekly": {"status": "ok", "percent": 0,
                   "resetsAt": "2026-08-17T00:00:00.681Z"},
        "monthly": {"status": "ok", "percent": 14,
                    "resetsAt": "2026-08-25T12:36:31.681Z"},
    },
}


def test_read_key_returns_none_when_missing(tmp_path):
    assert read_key(tmp_path / "nope.json") is None


def test_read_key_returns_none_on_garbage(tmp_path):
    p = tmp_path / "auth.json"
    p.write_text("not json{")
    assert read_key(p) is None


def test_read_key_reads_the_go_entry(tmp_path):
    p = tmp_path / "auth.json"
    p.write_text(json.dumps({
        "opencode-go": {"type": "api", "key": "sk-go"},
        "opencode": {"type": "api", "key": "sk-zen"},
    }))
    assert read_key(p) == "sk-go"


def test_read_key_returns_none_without_a_go_entry(tmp_path):
    p = tmp_path / "auth.json"
    p.write_text(json.dumps({"google": {"type": "oauth", "access": "x"}}))
    assert read_key(p) is None


def test_parse_reads_the_three_windows():
    snap = parse_usage(LIVE, now=100.0)
    assert [w.kind for w in snap.windows] == ["rolling", "weekly", "monthly"]
    assert snap.window("monthly").percent == 14.0
    assert snap.fetched_at == 100.0


def test_parse_keeps_reset_timestamps_as_utc():
    ts = parse_usage(LIVE, now=0.0).window("rolling").resets_at
    assert ts.tzinfo is not None
    assert ts.astimezone(timezone.utc).hour == 1


def test_parse_treats_every_window_as_active():
    # The endpoint reports no per-window active flag; all three always count.
    assert all(w.is_active for w in parse_usage(LIVE, now=0.0).windows)


def test_parse_derives_severity_from_percent():
    payload = {"usage": {
        "rolling": {"status": "ok", "percent": 50},
        "weekly": {"status": "ok", "percent": 85},
        "monthly": {"status": "ok", "percent": 97},
    }}
    sev = {w.kind: w.severity for w in parse_usage(payload, now=0.0).windows}
    assert sev == {"rolling": "normal", "weekly": "warning",
                   "monthly": "critical"}


def test_parse_drops_unreadable_entries_without_failing():
    payload = {"usage": {
        "rolling": {"percent": 12},
        "weekly": {"percent": None},
        "monthly": "garbage",
    }}
    snap = parse_usage(payload, now=0.0)
    assert [w.kind for w in snap.windows] == ["rolling"]


def test_parse_survives_a_missing_usage_object():
    assert parse_usage({}, now=0.0).windows == ()


def test_parse_ignores_unknown_window_names():
    payload = {"usage": {"rolling": {"percent": 5}, "daily": {"percent": 9}}}
    assert [w.kind for w in parse_usage(payload, now=0.0).windows] == ["rolling"]


class _Resp:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fetch_parses_a_good_response():
    def opener(req, timeout=None):
        assert req.get_header("Authorization") == "Bearer sk-go"
        return _Resp(json.dumps(LIVE).encode())
    snap = fetch_usage("sk-go", opener=opener, now=5.0)
    assert snap.window("monthly").percent == 14.0


def test_fetch_sends_a_user_agent():
    # Cloudflare fronts this endpoint and answers urllib's default agent with
    # 403 "error code: 1010". Verified live: any other agent gets a 200.
    seen = {}

    def opener(req, timeout=None):
        seen["ua"] = req.get_header("User-agent")
        return _Resp(json.dumps(LIVE).encode())
    fetch_usage("sk-go", opener=opener)
    assert seen["ua"]
    assert "urllib" not in seen["ua"]
    assert seen["ua"].startswith("aegis/")


def test_fetch_maps_401_to_unauthorized():
    def opener(req, timeout=None):
        raise urllib.error.HTTPError("u", 401, "no", {}, None)
    with pytest.raises(QuotaError) as e:
        fetch_usage("sk-go", opener=opener)
    assert e.value.kind == "unauthorized"


def test_fetch_maps_429_to_rate_limited():
    def opener(req, timeout=None):
        raise urllib.error.HTTPError("u", 429, "slow down", {}, None)
    with pytest.raises(QuotaError) as e:
        fetch_usage("sk-go", opener=opener)
    assert e.value.kind == "rate_limited"


def test_fetch_maps_500_to_unreachable():
    def opener(req, timeout=None):
        raise urllib.error.HTTPError("u", 500, "boom", {}, None)
    with pytest.raises(QuotaError) as e:
        fetch_usage("sk-go", opener=opener)
    assert e.value.kind == "unreachable"


def test_fetch_maps_truncated_body_to_unreachable():
    def opener(req, timeout=None):
        return _Resp(b'{"usage": {')
    with pytest.raises(QuotaError) as e:
        fetch_usage("sk-go", opener=opener)
    assert e.value.kind == "unreachable"
