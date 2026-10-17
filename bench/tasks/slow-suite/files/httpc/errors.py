class HTTPError(Exception):
    """The server answered with a status of 400 or more."""

    def __init__(self, status: int, body: bytes = b"", method: str = "", path: str = ""):
        super().__init__(f"{method} {path}: HTTP {status}".strip())
        self.status = status
        self.body = body


class ConnectionFailed(Exception):
    """No response arrived: the connection was refused, reset or timed out.

    The OSError that caused it is its __cause__.
    """
