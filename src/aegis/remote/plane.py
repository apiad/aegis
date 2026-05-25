"""Receiver-side HTTP plane for the aegis remote API.

Exposes endpoints that other aegis instances call:
  - ``POST /remote/v1/enqueue``  — enqueue work into this aegis's QueueManager.
  - ``POST /remote/v1/callback`` — deliver a task result to a local InboxRouter.

The app is a Starlette app; it is mounted onto a uvicorn server by
``aegis serve`` when ``.aegis.yaml`` has a ``remote_plane`` block.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Protocol

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from aegis.remote.config import RemotePlaneSpec
from aegis.scheduler.push import (
    list_payload, logs_payload, remove_schedule, show_payload,
    validate_spec, write_atomic,
)


class _QueueManagerLike(Protocol):
    def enqueue(self, queue: str, payload: str, *,
                enqueued_by: str, callback: bool,
                callback_to: str | None,
                callback_handle: str | None) -> tuple[str, int]: ...


def _check_auth(request: Request, spec: RemotePlaneSpec) -> dict | None:
    """Return None on auth success; else an {error: ..., _status: N} dict.

    Caller should pop ``_status`` and use it as the HTTP response code
    before returning JSON with the remaining keys.
    """
    if spec.accept_from:
        peer = request.client.host if request.client else None
        if peer not in spec.accept_from:
            return {"error": f"source ip {peer!r} not in accept_from",
                    "_status": 403}
    if spec.accept_tokens:
        auth = request.headers.get("authorization", "")
        token = (auth.removeprefix("Bearer ").strip()
                 if auth.startswith("Bearer ") else "")
        if token not in spec.accept_tokens:
            return {"error": "missing or invalid bearer token",
                    "_status": 401}
    return None


def build_plane(bridge, spec: RemotePlaneSpec) -> Starlette:
    """Build the Starlette app bound to ``bridge`` (or a bare queue_manager)
    and ``spec``.

    ``bridge`` may be either:
    - an object with ``.queue_manager`` and ``.inbox_router`` attributes
      (the preferred bridge shape), or
    - a plain queue-manager-like object (duck-typing back-compat for
      existing tests that pass ``_FakeQueueManager`` directly).
    """
    is_bridge = hasattr(bridge, "queue_manager")
    if is_bridge:
        queue_manager = bridge.queue_manager
        inbox_router = bridge.inbox_router
    else:
        queue_manager = bridge
        inbox_router = None

    async def enqueue(request: Request) -> JSONResponse:
        err = _check_auth(request, spec)
        if err:
            status = err.pop("_status", 401)
            return JSONResponse(err, status_code=status)

        try:
            body: dict[str, Any] = await request.json()
        except json.JSONDecodeError:
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)
        missing = [k for k in ("queue", "payload", "from") if k not in body]
        if missing:
            return JSONResponse(
                {"error": f"missing required fields: {missing}"},
                status_code=400)
        try:
            tid, pos = queue_manager.enqueue(
                body["queue"], body["payload"],
                enqueued_by=f"remote:{body['from']}",
                callback=False,
                callback_to=body.get("callback_to"),
                callback_handle=body.get("callback_handle"))
        except KeyError as e:
            return JSONResponse(
                {"error": f"unknown queue {e.args[0]!r}"},
                status_code=404)
        return JSONResponse({"task_id": tid, "queued_position": pos})

    async def callback(request: Request) -> Response:
        err = _check_auth(request, spec)
        if err:
            status = err.pop("_status", 401)
            return JSONResponse(err, status_code=status)
        if inbox_router is None:
            return JSONResponse(
                {"error": "callback endpoint requires inbox_router; "
                 "this plane is misconfigured"},
                status_code=500)
        try:
            body: dict[str, Any] = await request.json()
        except json.JSONDecodeError:
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)
        missing = [k for k in ("task_id", "queue", "from_peer", "to_handle",
                               "status", "result_text") if k not in body]
        if missing:
            return JSONResponse(
                {"error": f"missing required fields: {missing}"}, status_code=400)
        from aegis.queue.schema import InboxMessage, now_iso
        sender = f"queue:{body['from_peer']}:{body['queue']}"
        msg = InboxMessage(
            sender=sender,
            timestamp=now_iso(),
            body=body["result_text"],
            task_id=body["task_id"],
            status=body["status"],
        )
        await inbox_router.deliver(body["to_handle"], msg)
        return Response(status_code=204)

    async def schedule_push(request: Request) -> JSONResponse:
        err = _check_auth(request, spec)
        if err:
            status = err.pop("_status", 401)
            return JSONResponse(err, status_code=status)
        name = request.path_params["name"]
        if not name or "/" in name or name.startswith("."):
            return JSONResponse({"error": "invalid schedule name"},
                                status_code=400)
        try:
            body: dict[str, Any] = await request.json()
        except json.JSONDecodeError:
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)
        pushed_from = request.headers.get("X-Pushed-From", "peer:unknown")
        try:
            validate_spec(body, workflow_registry=bridge.workflow_registry)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        dest = write_atomic(bridge.state_root, name, body, pushed_from)
        return JSONResponse(
            {"name": name,
             "written_to": str(dest.relative_to(bridge.state_root))})

    async def schedule_list(request: Request) -> JSONResponse:
        err = _check_auth(request, spec)
        if err:
            status = err.pop("_status", 401)
            return JSONResponse(err, status_code=status)
        return JSONResponse(list_payload(
            getattr(bridge, "scheduler", None),
            bridge.state_root, bridge.inline_schedule_names()))

    async def schedule_show(request: Request) -> JSONResponse:
        err = _check_auth(request, spec)
        if err:
            status = err.pop("_status", 401)
            return JSONResponse(err, status_code=status)
        name = request.path_params["name"]
        payload = show_payload(
            getattr(bridge, "scheduler", None),
            bridge.state_root, bridge.inline_schedule_names(), name)
        if payload is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(payload)

    async def schedule_remove(request: Request) -> Response:
        err = _check_auth(request, spec)
        if err:
            status = err.pop("_status", 401)
            return JSONResponse(err, status_code=status)
        name = request.path_params["name"]
        ok, error = remove_schedule(
            getattr(bridge, "scheduler", None),
            bridge.state_root, bridge.inline_schedule_names(), name)
        if error == "not found":
            return JSONResponse({"error": "not found"}, status_code=404)
        if error is not None:
            return JSONResponse({"error": error}, status_code=409)
        return Response(status_code=204)

    async def schedule_logs(request: Request) -> JSONResponse:
        err = _check_auth(request, spec)
        if err:
            status = err.pop("_status", 401)
            return JSONResponse(err, status_code=status)
        name = request.path_params["name"]
        try:
            tail = int(request.query_params.get("tail", "50"))
        except ValueError:
            return JSONResponse({"error": "invalid tail"}, status_code=400)
        return JSONResponse(logs_payload(bridge.state_root, name, tail=tail))

    routes = [
        Route("/remote/v1/enqueue", enqueue, methods=["POST"]),
        Route("/remote/v1/callback", callback, methods=["POST"]),
    ]
    if is_bridge:
        routes.append(
            Route("/remote/v1/schedule/{name}", schedule_push, methods=["PUT"]))
        routes.append(
            Route("/remote/v1/schedule", schedule_list, methods=["GET"]))
        routes.append(
            Route("/remote/v1/schedule/{name}", schedule_show, methods=["GET"]))
        routes.append(
            Route("/remote/v1/schedule/{name}", schedule_remove,
                  methods=["DELETE"]))
        routes.append(
            Route("/remote/v1/schedule/{name}/logs", schedule_logs,
                  methods=["GET"]))
    return Starlette(routes=routes)


def run_plane_async(app: Starlette, bind: str) -> asyncio.Task:
    """Run the plane on ``bind`` (``host:port``) as an asyncio task.

    Returns the task; caller is responsible for keeping a reference
    and (optionally) cancelling on shutdown.
    """
    host, _, port_s = bind.rpartition(":")
    config = uvicorn.Config(
        app, host=host, port=int(port_s),
        log_level="info", access_log=False)
    server = uvicorn.Server(config)
    return asyncio.create_task(server.serve())
