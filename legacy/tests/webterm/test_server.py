from aegis.config import WebConfig
from aegis.webterm.server import resolve_port, uvicorn_config


def test_a_configured_port_wins(tmp_path):
    assert resolve_port(WebConfig(port=8123), tmp_path) == 8123


def test_an_unset_port_is_picked_once_and_reused(tmp_path):
    first = resolve_port(WebConfig(), tmp_path)
    assert (tmp_path / "web.port").read_text() == str(first)
    assert resolve_port(WebConfig(), tmp_path) == first


def test_the_access_log_is_off():
    """The login URL carries the token once; an access log is where it
    would stay."""
    assert uvicorn_config(object(), bind="127.0.0.1", port=1).access_log is False
