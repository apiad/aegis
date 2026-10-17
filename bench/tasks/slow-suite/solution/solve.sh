#!/bin/bash
# Oracle: split request() into one attempt and a retry loop around it.
set -euo pipefail
cd "$1"
python3 - <<'PY'
import pathlib

p = pathlib.Path("httpc/client.py")
s = p.read_text()
s = s.replace("import http.client\nimport json\n", "import http.client\nimport json\nimport time\n")
s = s.replace("from .response import Response\n", '''from .response import Response

RETRY_STATUSES = {502, 503, 504}
ATTEMPTS = 3
BACKOFF_S = 0.2


def _retryable(e: Exception) -> bool:
    if isinstance(e, HTTPError):
        return e.status in RETRY_STATUSES
    return isinstance(e.__cause__, ConnectionResetError)
''')
s = s.replace('''    def request(self, method: str, path: str, body=None) -> Response:
        """Send one request. A status of 400 or more raises HTTPError; no response
        at all raises ConnectionFailed."""
''', '''    def request(self, method: str, path: str, body=None) -> Response:
        """Send a request, retrying a 502, 503, 504 or a reset connection up to
        ATTEMPTS times in all, waiting BACKOFF_S, then twice that, between them."""
        for attempt in range(ATTEMPTS):
            try:
                return self._attempt(method, path, body)
            except (HTTPError, ConnectionFailed) as e:
                if attempt == ATTEMPTS - 1 or not _retryable(e):
                    raise
            time.sleep(BACKOFF_S * 2**attempt)
        raise AssertionError("unreachable")

    def _attempt(self, method: str, path: str, body) -> Response:
        """Send one request. A status of 400 or more raises HTTPError; no response
        at all raises ConnectionFailed."""
''')
p.write_text(s)
PY
