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
                callback_handle: str | None) -> tuple[str, int] | dict: ...


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
            result = queue_manager.enqueue(
                body["queue"], body["payload"],
                enqueued_by=f"remote:{body['from']}",
                callback=False,
                callback_to=body.get("callback_to"),
                callback_handle=body.get("callback_handle"))
        except KeyError as e:
            return JSONResponse(
                {"error": f"unknown queue {e.args[0]!r}"},
                status_code=404)
        if isinstance(result, dict):
            return JSONResponse(result, status_code=429)
        tid, pos = result
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
        result = remove_schedule(
            getattr(bridge, "scheduler", None),
            bridge.state_root, bridge.inline_schedule_names(), name)
        if result.status == "ok":
            return Response(status_code=204)
        if result.status == "not_found":
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(
            {"error": f"cannot remove {result.source!r}-source schedule"},
            status_code=409)

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

    async def budget_list(request: Request) -> JSONResponse:
        err = _check_auth(request, spec)
        if err:
            status = err.pop("_status", 401)
            return JSONResponse(err, status_code=status)
        from datetime import datetime, timezone
        from aegis.budget.evaluator import evaluate_budgets

        qm = bridge.queue_manager
        now = datetime.now(timezone.utc)
        rows = []
        for name, q in qm._queues.items():
            if not q.budgets:
                rows.append({"name": name, "budgets_count": 0,
                              "status": "no-budget", "binding": None,
                              "unblock_at": None})
                continue
            tail = qm._load_recent_jsonl(
                name, max_age=max(b.window for b in q.budgets))
            d = evaluate_budgets(tail, q.budgets, now)
            if d.allowed:
                tightest = min(
                    d.checks,
                    key=lambda c: (c.headroom / c.limit) if c.limit > 0 else 0)
                binding = (f"${tightest.spent} of ${tightest.limit} "
                            f"/ {tightest.window_str}"
                            if tightest.constraint == "usd"
                            else f"{tightest.spent} of {tightest.limit} "
                                  f"{tightest.constraint} / {tightest.window_str}")
                rows.append({"name": name, "budgets_count": len(q.budgets),
                              "status": "ok", "binding": binding,
                              "unblock_at": None})
            else:
                c = d.blocked_by[0]
                binding = (f"${c.spent} of ${c.limit} / {c.window_str}"
                            if c.constraint == "usd"
                            else f"{c.spent} of {c.limit} "
                                  f"{c.constraint} / {c.window_str}")
                rows.append({"name": name, "budgets_count": len(q.budgets),
                              "status": "blocked", "binding": binding,
                              "unblock_at": d.unblock_at.isoformat().replace(
                                  "+00:00", "Z") if d.unblock_at else None})
        return JSONResponse({"queues": rows})

    async def budget_show(request: Request) -> JSONResponse:
        err = _check_auth(request, spec)
        if err:
            status = err.pop("_status", 401)
            return JSONResponse(err, status_code=status)
        name = request.path_params["queue"]
        qm = bridge.queue_manager
        if name not in qm._queues:
            return JSONResponse({"error": "unknown queue"}, status_code=404)
        q = qm._queues[name]
        from datetime import datetime, timezone
        from aegis.budget.evaluator import evaluate_budgets
        now = datetime.now(timezone.utc)
        if not q.budgets:
            return JSONResponse({"name": name, "allowed": True, "checks": [],
                                  "blocked_by": [], "unblock_at": None})
        tail = qm._load_recent_jsonl(
            name, max_age=max(b.window for b in q.budgets))
        d = evaluate_budgets(tail, q.budgets, now)

        def _ser(c):
            return {"constraint": c.constraint, "limit": str(c.limit),
                    "spent": str(c.spent), "window": c.window_str,
                    "window_start": c.window_start.isoformat().replace(
                        "+00:00", "Z"),
                    "allowed": c.allowed, "headroom": str(c.headroom),
                    "unblock_at": c.unblock_at.isoformat().replace(
                        "+00:00", "Z") if c.unblock_at else None}
        return JSONResponse({
            "name": name, "allowed": d.allowed,
            "checks": [_ser(c) for c in d.checks],
            "blocked_by": [_ser(c) for c in d.blocked_by],
            "unblock_at": d.unblock_at.isoformat().replace("+00:00", "Z")
                           if d.unblock_at else None,
        })

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
    routes.append(Route("/remote/v1/budget", budget_list, methods=["GET"]))
    routes.append(
        Route("/remote/v1/budget/{queue}", budget_show, methods=["GET"]))
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
