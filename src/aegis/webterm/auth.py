"""One secret, presented once in a URL and thereafter as a cookie."""
from __future__ import annotations

import hmac

COOKIE = "aegis_web"


def token_ok(presented: str | None, token: str) -> bool:
    """Constant-time, and never true for an empty token on either side:
    an unset token must lock the door, not open it."""
    if not presented or not token:
        return False
    return hmac.compare_digest(presented.encode(), token.encode())
