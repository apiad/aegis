import pytest

from httpc import Client, Response
from httpc.auth import auth_headers


def test_only_http_is_supported():
    with pytest.raises(ValueError):
        Client("https://gateway.example")


def test_auth_headers():
    assert auth_headers("t0k", "north") == {"Authorization": "Bearer t0k", "X-Tenant": "north"}
    assert auth_headers(None) == {}


def test_response_json_and_ok():
    r = Response(204)
    assert r.json() is None and r.ok
    assert Response(200, body=b'{"a": 1}').json() == {"a": 1}
