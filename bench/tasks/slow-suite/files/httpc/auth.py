"""Credentials: the gateway takes a bearer token, optionally scoped to a tenant."""


def auth_headers(token: str | None, tenant: str | None = None) -> dict[str, str]:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if tenant:
        headers["X-Tenant"] = tenant
    return headers
