from aegis.events import Event, SessionClosed, SessionMeta
from aegis.state.event_codec import decode_event, encode_event


def test_session_meta_is_in_event_sum():
    m = SessionMeta(
        handle="lucid-knuth",
        profile="claude-sonnet",
        provider="claude-code",
        cwd="/tmp/proj",
        created_at="2026-05-28T14:00:00Z",
        origin="tui",
        preview="",
    )
    assert isinstance(m, Event.__args__)


def test_session_closed_is_in_event_sum():
    c = SessionClosed(
        closed_at="2026-05-28T15:00:00Z", reason="user")
    assert isinstance(c, Event.__args__)


def test_session_closed_reason_round_trips_known_values():
    for r in ("user", "interrupt", "crash"):
        c = SessionClosed(closed_at="2026-05-28T15:00:00Z", reason=r)
        assert c.reason == r


def test_session_meta_codec_roundtrip():
    m = SessionMeta(
        handle="lucid-knuth", profile="claude-sonnet",
        provider="claude-code", cwd="/tmp/proj",
        created_at="2026-05-28T14:00:00Z", origin="tui",
        preview="hello world",
    )
    assert decode_event(encode_event(m)) == m


def test_session_closed_codec_roundtrip():
    c = SessionClosed(closed_at="2026-05-28T15:00:00Z", reason="user")
    assert decode_event(encode_event(c)) == c
