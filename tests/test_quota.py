import json
import urllib.error
from datetime import timezone

import pytest

from aegis.usage.quota import QuotaError
from aegis.usage.quota_claude import fetch_quota, parse_quota, read_token

# Trimmed from a real response — note the null-heavy optional fields.
LIVE = {
    "five_hour": {"utilization": 64.0,
                  "resets_at": "2026-07-24T17:29:59.110568+00:00"},
    "seven_day": {"utilization": 7.0,
                  "resets_at": "2026-07-31T05:59:59.110600+00:00"},
    "seven_day_opus": None,
    "limits": [
        {"kind": "session", "group": "session", "percent": 64,
         "severity": "normal", "resets_at": "2026-07-24T17:29:59.110568+00:00",
         "scope": None, "is_active": True},
        {"kind": "weekly_all", "group": "weekly", "percent": 7,
         "severity": "normal", "resets_at": "2026-07-31T05:59:59.110600+00:00",
         "scope": None, "is_active": False},
    ],
}


def test_read_token_returns_none_when_missing(tmp_path):
    assert read_token(tmp_path / "nope.json") is None


def test_read_token_returns_none_on_garbage(tmp_path):
    p = tmp_path / "creds.json"
    p.write_text("not json{")
    assert read_token(p) is None


def test_read_token_reads_the_oauth_field(tmp_path):
    p = tmp_path / "creds.json"
    p.write_text(json.dumps({"claudeAiOauth": {"accessToken": "tok-123"}}))
    assert read_token(p) == "tok-123"


def test_parse_reads_the_limits_array():
    snap = parse_quota(LIVE, now=100.0)
    assert [w.kind for w in snap.windows] == ["session", "weekly_all"]
    assert snap.window("session").percent == 64.0
    assert snap.window("session").is_active is True
    assert snap.fetched_at == 100.0


def test_parse_keeps_reset_timestamps_as_utc():
    snap = parse_quota(LIVE, now=0.0)
    ts = snap.window("session").resets_at
    assert ts.tzinfo is not None
    assert ts.astimezone(timezone.utc).hour == 17


def test_parse_drops_unreadable_entries_without_failing():
    payload = {"limits": [
        {"kind": "session", "percent": 12},
        {"kind": None, "percent": 3},
        {"percent": 9},
        "garbage",
        {"kind": "weekly_all", "percent": None},
    ]}
    snap = parse_quota(payload, now=0.0)
    assert [w.kind for w in snap.windows] == ["session"]


def test_parse_falls_back_to_local_severity():
    payload = {"limits": [
        {"kind": "a", "percent": 50},
        {"kind": "b", "percent": 85},
        {"kind": "c", "percent": 97},
    ]}
    sev = {w.kind: w.severity for w in parse_quota(payload, now=0.0).windows}
    assert sev == {"a": "normal", "b": "warning", "c": "critical"}


def test_parse_prefers_the_api_severity():
    payload = {"limits": [{"kind": "a", "percent": 10,
                           "severity": "critical"}]}
    assert parse_quota(payload, now=0.0).windows[0].severity == "critical"


def test_parse_survives_a_missing_limits_array():
    assert parse_quota({}, now=0.0).windows == ()


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
        assert req.get_header("Authorization") == "Bearer tok"
        assert req.get_header("Anthropic-beta") == "oauth-2025-04-20"
        return _Resp(json.dumps(LIVE).encode())
    snap = fetch_quota("tok", opener=opener, now=5.0)
    assert snap.window("session").percent == 64.0


def test_fetch_maps_401_to_unauthorized():
    def opener(req, timeout=None):
        raise urllib.error.HTTPError("u", 401, "no", {}, None)
    with pytest.raises(QuotaError) as e:
        fetch_quota("tok", opener=opener)
    assert e.value.kind == "unauthorized"


def test_fetch_maps_500_to_unreachable():
    def opener(req, timeout=None):
        raise urllib.error.HTTPError("u", 500, "boom", {}, None)
    with pytest.raises(QuotaError) as e:
        fetch_quota("tok", opener=opener)
    assert e.value.kind == "unreachable"


def test_fetch_maps_timeout_to_unreachable():
    def opener(req, timeout=None):
        raise TimeoutError
    with pytest.raises(QuotaError) as e:
        fetch_quota("tok", opener=opener)
    assert e.value.kind == "unreachable"


def test_fetch_maps_truncated_body_to_unreachable():
    def opener(req, timeout=None):
        return _Resp(b'{"limits": [')
    with pytest.raises(QuotaError) as e:
        fetch_quota("tok", opener=opener)
    assert e.value.kind == "unreachable"


def test_fetch_maps_429_to_rate_limited():
    def opener(req, timeout=None):
        raise urllib.error.HTTPError("u", 429, "slow down", {}, None)
    with pytest.raises(QuotaError) as e:
        fetch_quota("tok", opener=opener)
    assert e.value.kind == "rate_limited"
