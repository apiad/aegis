"""The pure extractor: raw events in, exchanges out.

An exchange is one operator turn plus the agent's response arc up to the
next one. The distinctions that matter are all about what counts as a
*boundary*: a monitor wake is the same piece of work continuing, while a
peer handoff carries new intent and starts a new one.
"""
from aegis.corpus.extract import extract_exchanges, BOUNDARY_SOURCES


def test_operator_turn_starts_an_exchange():
    evs = [
        ({"t": "UserMessage", "text": "fix the geocoder", "source": "operator"}, "2026-07-01T10:00:00Z"),
        ({"t": "AssistantText", "text": "on it"}, "2026-07-01T10:00:05Z"),
    ]
    out = extract_exchanges(evs, meta={"handle": "h1", "cwd": "/w"})
    assert len(out) == 1
    assert out[0].operator_text == "fix the geocoder"
    assert out[0].assistant_text == "on it"
    assert out[0].handle == "h1"


def test_monitor_wake_attaches_instead_of_splitting():
    evs = [
        ({"t": "UserMessage", "text": "build it", "source": "operator"}, "2026-07-01T10:00:00Z"),
        ({"t": "AssistantText", "text": "building"}, "2026-07-01T10:00:01Z"),
        ({"t": "UserMessage", "text": "> from monitor:X done", "source": "monitor"}, "2026-07-01T10:05:00Z"),
        ({"t": "AssistantText", "text": "green"}, "2026-07-01T10:05:01Z"),
    ]
    out = extract_exchanges(evs, meta={"handle": "h1", "cwd": "/w"})
    assert len(out) == 1, "a monitor wake is a continuation, not a new question"
    assert "green" in out[0].assistant_text


def test_agent_handoff_starts_a_new_exchange():
    evs = [
        ({"t": "UserMessage", "text": "build it", "source": "operator"}, "2026-07-01T10:00:00Z"),
        ({"t": "UserMessage", "text": "> from agent:peer take over", "source": "agent"}, "2026-07-01T10:01:00Z"),
    ]
    out = extract_exchanges(evs, meta={"handle": "h1", "cwd": "/w"})
    assert len(out) == 2, "a handoff carries new intent"
    assert BOUNDARY_SOURCES == frozenset({"operator", "agent"})


def test_file_and_tool_facets_are_collected():
    evs = [
        ({"t": "UserMessage", "text": "edit it", "source": "operator"}, "2026-07-01T10:00:00Z"),
        ({"t": "ToolUse", "name": "Edit", "input": {"file_path": "/w/a.py"}}, "2026-07-01T10:00:01Z"),
        ({"t": "ToolUse", "name": "Bash", "input": {"command": "ls"}}, "2026-07-01T10:00:02Z"),
    ]
    out = extract_exchanges(evs, meta={"handle": "h1", "cwd": "/w"})
    assert out[0].files_touched == ("/w/a.py",)
    assert set(out[0].tools_used) == {"Edit", "Bash"}


def test_file_facets_read_the_field_real_logs_actually_carry():
    """Persisted `ToolUse` events carry **`raw_input`**, not `input`. Across
    19,008 real events there is no `input` key at all, so reading it gave an
    always-empty `files_touched` — and that is the facet that makes "when
    were we working on the geocoder" a path lookup rather than a semantic
    query. Captured from the real log, not assumed: a synthetic fixture that
    invents the field name only re-asserts the mistake.
    """
    evs = [
        ({"t": "UserMessage", "text": "edit it", "source": "operator"}, "2026-07-01T10:00:00Z"),
        ({"t": "ToolUse", "name": "Read", "kind": "read",
          "raw_input": {"file_path": "/w/real.py"}}, "2026-07-01T10:00:01Z"),
    ]
    out = extract_exchanges(evs, meta={"handle": "h1", "cwd": "/w"})
    assert out[0].files_touched == ("/w/real.py",)


def test_locations_also_contribute_file_facets():
    """`locations` is `[[path, line], ...]` and is populated by the drivers
    for file-ish tools — a second real source of the same facet."""
    evs = [
        ({"t": "UserMessage", "text": "read it", "source": "operator"}, "2026-07-01T10:00:00Z"),
        ({"t": "ToolUse", "name": "Read", "kind": "read",
          "locations": [["/w/from-locations.md", None]]}, "2026-07-01T10:00:01Z"),
    ]
    out = extract_exchanges(evs, meta={"handle": "h1", "cwd": "/w"})
    assert "/w/from-locations.md" in out[0].files_touched


def test_tool_results_are_excluded_from_text():
    evs = [
        ({"t": "UserMessage", "text": "read it", "source": "operator"}, "2026-07-01T10:00:00Z"),
        ({"t": "ToolResult", "content": "a" * 5000}, "2026-07-01T10:00:01Z"),
    ]
    out = extract_exchanges(evs, meta={"handle": "h1", "cwd": "/w"})
    assert "aaaa" not in out[0].assistant_text
