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
                         from_: str, *,
                         callback_to: str | None = None,
                         callback_handle: str | None = None) -> dict:
    """POST one enqueue to the remote plane at ``spec.url``.

    ``callback_to`` and ``callback_handle`` are optional hints that tell the
    receiving serve to POST the worker's final result back to the caller's
    inbox. When omitted (or ``None``), the receiving serve applies its own
    default completion behavior (fire-and-forget).

    Returns the parsed response dict on success, augmented with
    ``target_url`` for caller debugging. On any failure, returns
    ``{"error": "..."}`` with a normalized, human-readable message —
    never raises.
    """
    client = await _build_client(spec)
    try:
        try:
            body: dict = {"queue": queue, "payload": payload, "from": from_}
            if callback_to is not None:
                body["callback_to"] = callback_to
            if callback_handle is not None:
                body["callback_handle"] = callback_handle
            resp = await client.post("/remote/v1/enqueue", json=body)
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


def _normalize_err(prefix: str, resp) -> dict:
    try:
        err = resp.json().get("error", resp.text)
    except ValueError:
        err = resp.text
    return {"error": f"{prefix} returned {resp.status_code}: {err}"}


async def _schedule_request(spec: RemoteSpec, method: str, path: str,
                            prefix: str, *,
                            json_body: dict | None = None,
                            headers: dict | None = None,
                            params: dict | None = None,
                            success_codes: tuple[int, ...] = (200,)) -> dict:
    client = await _build_client(spec)
    try:
        try:
            resp = await client.request(
                method, path, json=json_body,
                headers=headers, params=params)
        except httpx.ConnectError as e:
            return {"error": f"{prefix}: remote unreachable: {e}"}
        except httpx.TimeoutException as e:
            return {"error": f"{prefix}: remote timed out: {e}"}
        except httpx.HTTPError as e:
            return {"error": f"{prefix}: remote http error: {e}"}
        if resp.status_code in success_codes:
            if resp.status_code == 204 or not resp.content:
                return {"ok": True}
            return resp.json()
        return _normalize_err(prefix, resp)
    finally:
        await client.aclose()


async def remote_budget_list(spec: RemoteSpec) -> dict:
    async with await _build_client(spec) as client:
        r = await client.get("/remote/v1/budget")
    if r.status_code == 200:
        return r.json()
    return _normalize_err("budget list", r)


async def remote_budget_show(spec: RemoteSpec, queue: str) -> dict:
    async with await _build_client(spec) as client:
        r = await client.get(f"/remote/v1/budget/{queue}")
    if r.status_code == 200:
        return r.json()
    return _normalize_err("budget show", r)


async def remote_schedule_push(spec: RemoteSpec, *, name: str,
                               spec_body: dict, pushed_from: str) -> dict:
    return await _schedule_request(
        spec, "PUT", f"/remote/v1/schedule/{name}", "schedule push",
        json_body=spec_body, headers={"X-Pushed-From": pushed_from})


async def remote_schedule_list(spec: RemoteSpec) -> dict:
    return await _schedule_request(
        spec, "GET", "/remote/v1/schedule", "schedule list")


async def remote_schedule_show(spec: RemoteSpec, name: str) -> dict:
    return await _schedule_request(
        spec, "GET", f"/remote/v1/schedule/{name}", "schedule show")


async def remote_schedule_remove(spec: RemoteSpec, name: str) -> dict:
    return await _schedule_request(
        spec, "DELETE", f"/remote/v1/schedule/{name}", "schedule remove",
        success_codes=(200, 204))


async def remote_schedule_logs(spec: RemoteSpec, name: str,
                               tail: int = 50) -> dict:
    return await _schedule_request(
        spec, "GET", f"/remote/v1/schedule/{name}/logs", "schedule logs",
        params={"tail": str(tail)})
