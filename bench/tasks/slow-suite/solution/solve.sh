#!/bin/bash
# Oracle: split request() into one attempt and a retry loop around it, driven by
# a Retry policy, with a 503's Retry-After in place of the backoff up to 2 s.
set -euo pipefail
cd "$1"
python3 - <<'PY'
import pathlib

p = pathlib.Path("httpc/client.py")
s = p.read_text()
s = s.replace("import http.client\nimport json\n", "import http.client\nimport json\nimport time\nfrom dataclasses import dataclass\n")
s = s.replace("from .response import Response\n", '''from .response import Response

RETRY_STATUSES = {502, 503, 504}
MAX_RETRY_AFTER_S = 2.0


@dataclass(frozen=True)
class Retry:
    """Up to ``attempts`` tries in all, waiting backoff_s, then twice that, ..."""

    attempts: int = 3
    backoff_s: float = 0.2


def _retryable(e: Exception) -> bool:
    if isinstance(e, HTTPError):
        return e.status in RETRY_STATUSES
    return isinstance(e.__cause__, ConnectionResetError)


def _delay(e: Exception, backoff: float) -> float:
    after = getattr(e, "retry_after", None)
    if isinstance(e, HTTPError) and e.status == 503 and after is not None and after.isdigit():
        return min(float(after), MAX_RETRY_AFTER_S)
    return backoff
''')
s = s.replace("timeout: float = 5.0):", "timeout: float = 5.0, retry: Retry | None = None):")
s = s.replace("        self.timeout = timeout\n", "        self.timeout = timeout\n        self.retry = retry or Retry()\n")
s = s.replace('''    def request(self, method: str, path: str, body=None) -> Response:
        """Send one request. A status of 400 or more raises HTTPError; no response
        at all raises ConnectionFailed."""
''', '''    def request(self, method: str, path: str, body=None) -> Response:
        """Send a request, retrying a 502, 503, 504 or a reset connection as the
        Retry policy says."""
        for attempt in range(self.retry.attempts):
            try:
                return self._attempt(method, path, body)
            except (HTTPError, ConnectionFailed) as e:
                if attempt == self.retry.attempts - 1 or not _retryable(e):
                    raise
                time.sleep(_delay(e, self.retry.backoff_s * 2**attempt))
        raise AssertionError("unreachable")

    def _attempt(self, method: str, path: str, body) -> Response:
        """Send one request. A status of 400 or more raises HTTPError; no response
        at all raises ConnectionFailed."""
''')
old = "            raise HTTPError(resp.status, data, method, path)\n"
assert old in s
s = s.replace(old, '''            e = HTTPError(resp.status, data, method, path)
            e.retry_after = resp.getheader("Retry-After")
            raise e
''')
p.write_text(s)
i = pathlib.Path("httpc/__init__.py")
t = i.read_text().replace("from .client import Client\n", "from .client import Client, Retry\n")
i.write_text(t.replace('__all__ = ["Client",', '__all__ = ["Client", "Retry",'))
PY
