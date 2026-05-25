"""Receiver-side HTTP plane for the aegis remote API.

Exposes a single endpoint, ``POST /remote/v1/enqueue``, that other
aegis instances call to enqueue work into this aegis's QueueManager.

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
from starlette.responses import JSONResponse
from starlette.routing import Route

from aegis.remote.config import RemotePlaneSpec


class _QueueManagerLike(Protocol):
    def enqueue(self, queue: str, payload: str, *,
                enqueued_by: str, callback: bool) -> tuple[str, int]: ...


def build_plane(queue_manager: _QueueManagerLike,
                spec: RemotePlaneSpec) -> Starlette:
    """Build the Starlette app bound to ``queue_manager`` + ``spec``."""

    async def enqueue(request: Request) -> JSONResponse:
        if spec.accept_from:
            peer = request.client.host if request.client else None
            if peer not in spec.accept_from:
                return JSONResponse(
                    {"error": f"source ip {peer!r} not in accept_from"},
                    status_code=403)
        if spec.accept_tokens:
            auth = request.headers.get("authorization", "")
            token = (auth.removeprefix("Bearer ").strip()
                     if auth.startswith("Bearer ") else "")
            if token not in spec.accept_tokens:
                return JSONResponse(
                    {"error": "missing or invalid bearer token"},
                    status_code=401)

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
                callback=False)
        except KeyError as e:
            return JSONResponse(
                {"error": f"unknown queue {e.args[0]!r}"},
                status_code=404)
        return JSONResponse({"task_id": tid, "queued_position": pos})

    return Starlette(routes=[
        Route("/remote/v1/enqueue", enqueue, methods=["POST"]),
    ])


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
