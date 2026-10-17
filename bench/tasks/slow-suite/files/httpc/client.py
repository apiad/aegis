import http.client
import json
from urllib.parse import urlsplit

from .auth import auth_headers
from .errors import ConnectionFailed, HTTPError
from .response import Response


class Client:
    """One gateway, one base URL. Every call opens a fresh connection."""

    def __init__(self, base_url: str, token: str | None = None, tenant: str | None = None, timeout: float = 5.0):
        parts = urlsplit(base_url)
        if parts.scheme != "http":
            raise ValueError(f"only http:// is supported, got {base_url!r}")
        self.host = parts.hostname
        self.port = parts.port or 80
        self.prefix = parts.path.rstrip("/")
        self.timeout = timeout
        self.headers = {"Accept": "application/json", **auth_headers(token, tenant)}

    def request(self, method: str, path: str, body=None) -> Response:
        """Send one request. A status of 400 or more raises HTTPError; no response
        at all raises ConnectionFailed."""
        payload = None if body is None else json.dumps(body).encode()
        headers = dict(self.headers)
        if payload is not None:
            headers["Content-Type"] = "application/json"
        conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        try:
            conn.request(method, self.prefix + path, body=payload, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
        except OSError as e:
            raise ConnectionFailed(f"{method} {path}: {e}") from e
        finally:
            conn.close()
        if resp.status >= 400:
            raise HTTPError(resp.status, data, method, path)
        return Response(resp.status, dict(resp.getheaders()), data)

    def get(self, path: str) -> Response:
        return self.request("GET", path)

    def post(self, path: str, body) -> Response:
        return self.request("POST", path, body)

    def put(self, path: str, body) -> Response:
        return self.request("PUT", path, body)

    def delete(self, path: str) -> Response:
        return self.request("DELETE", path)
