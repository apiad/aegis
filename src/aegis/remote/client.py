"""Caller-side httpx client for the remote plane."""
from __future__ import annotations

import httpx

from aegis.remote.config import RemoteSpec

_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)


async def _build_client(spec: RemoteSpec) -> httpx.AsyncClient:
    """Construct the httpx client for ``spec``. Separated so tests can
    monkeypatch this to inject an ASGI transport against an in-process
    plane app.
    """
    headers: dict[str, str] = {}
    if spec.token:
        headers["Authorization"] = f"Bearer {spec.token}"
    return httpx.AsyncClient(
        base_url=spec.url, headers=headers, timeout=_DEFAULT_TIMEOUT)


async def remote_enqueue(spec: RemoteSpec, queue: str, payload: str,
                         from_: str) -> dict:
    """POST one enqueue to the remote plane at ``spec.url``.

    Returns the parsed response dict on success, augmented with
    ``target_url`` for caller debugging. On any failure, returns
    ``{"error": "..."}`` with a normalized, human-readable message —
    never raises.
    """
    client = await _build_client(spec)
    try:
        try:
            resp = await client.post(
                "/remote/v1/enqueue",
                json={"queue": queue, "payload": payload, "from": from_})
        except httpx.ConnectError as e:
            return {"error": f"remote unreachable (connection refused): {e}"}
        except httpx.TimeoutException as e:
            return {"error": f"remote timed out: {e}"}
        except httpx.HTTPError as e:
            return {"error": f"remote http error: {e}"}

        if resp.status_code == 200:
            body = resp.json()
            return {**body, "target_url": spec.url}
        try:
            err = resp.json().get("error", resp.text)
        except ValueError:
            err = resp.text
        return {"error": f"remote returned {resp.status_code}: {err}"}
    finally:
        await client.aclose()


async def remote_callback(spec: RemoteSpec, body: dict) -> dict:
    """POST /remote/v1/callback. Best-effort, no retry."""
    try:
        async with await _build_client(spec) as client:
            r = await client.post("/remote/v1/callback", json=body,
                                  timeout=httpx.Timeout(10.0, connect=5.0))
    except httpx.TimeoutException:
        return {"error": "callback_dropped: timeout"}
    except httpx.RequestError as e:
        return {"error": f"callback_dropped: unreachable ({e})"}
    if r.status_code == 204 or r.status_code == 200:
        return {"ok": True}
    if r.status_code == 401:
        return {"error": "callback_dropped: auth_rejected"}
    return {"error": f"callback_dropped: {r.status_code}"}
