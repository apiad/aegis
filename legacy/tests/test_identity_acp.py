from __future__ import annotations

from aegis.drivers.acp import AcpSession
from aegis.mcp.identity import HEADER


def _entry(token: str, url: str = "http://x/mcp/") -> dict:
    sess = AcpSession.__new__(AcpSession)
    sess._mcp_url = url
    sess._token = token
    return sess._mcp_servers()[0]


def test_headers_are_a_list_of_name_value_pairs():
    """acp.schema.McpServerHttp.headers is List[HttpHeader], and
    HttpHeader has `name` and `value`. A mapping is the wrong shape."""
    entry = _entry("tok123")
    assert isinstance(entry["headers"], list)
    assert entry["headers"] == [{"name": "X-Aegis-Session",
                                 "value": "tok123"}]


def test_the_header_name_matches_what_the_server_looks_for():
    assert _entry("tok123")["headers"][0]["name"].lower() == HEADER


def test_no_token_means_no_headers():
    assert _entry("")["headers"] == []


def test_no_url_means_no_servers():
    sess = AcpSession.__new__(AcpSession)
    sess._mcp_url = ""
    sess._token = "tok123"
    assert sess._mcp_servers() == []


def test_the_entry_keeps_the_shape_the_agent_expects():
    """Regression guard on the fields beside `headers` — this construction
    used to be inline, and extracting it must not drop one."""
    entry = _entry("tok123", "http://x/mcp/")
    assert entry["type"] == "http"
    assert entry["name"] == "aegis"
    assert entry["url"] == "http://x/mcp/"


def test_a_session_built_normally_carries_its_token():
    """`_mcp_servers` reads `self._token`, so __init__ has to set it —
    a session constructed the real way, not via __new__."""
    from aegis.config import Agent
    agent = Agent(harness="lovelaice", model="m", effort="medium",
                  permission="auto")
    sess = AcpSession(agent, ".", "http://x/mcp/", "alice", token="tok123")
    assert sess._mcp_servers()[0]["headers"] == [
        {"name": "X-Aegis-Session", "value": "tok123"}]


def test_a_session_without_a_token_still_builds():
    from aegis.config import Agent
    agent = Agent(harness="lovelaice", model="m", effort="medium",
                  permission="auto")
    sess = AcpSession(agent, ".", "http://x/mcp/", "alice")
    assert sess._mcp_servers()[0]["headers"] == []
