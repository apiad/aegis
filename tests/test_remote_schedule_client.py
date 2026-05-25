"""Unit tests for the outbound schedule client functions.

Uses the project standard ASGITransport pattern (no pytest-httpx). Each
test builds a tiny Starlette stub app that records the incoming request
and emits the expected response shape.
"""
from __future__ import annotations

import json

import httpx
import pytest
from httpx import ASGITransport
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from aegis.remote.client import (
    remote_schedule_list,
    remote_schedule_logs,
    remote_schedule_push,
    remote_schedule_remove,
    remote_schedule_show,
)
from aegis.remote.config import RemoteSpec


def _wire(monkeypatch, app, base_url="http://test"):
    async def _factory(_: RemoteSpec) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url=base_url)
    monkeypatch.setattr(
        "aegis.remote.client._build_client", _factory)


@pytest.mark.asyncio
async def test_remote_schedule_push_sends_pushed_from_header(monkeypatch):
    captured: dict = {}

    async def handler(request: Request) -> JSONResponse:
        captured["name"] = request.path_params["name"]
        captured["header"] = request.headers.get("x-pushed-from")
        captured["body"] = json.loads(await request.body())
        return JSONResponse({"name": captured["name"],
                             "written_to": ".aegis/schedules/foo.yaml"})

    app = Starlette(routes=[
        Route("/remote/v1/schedule/{name}", handler, methods=["PUT"])])
    _wire(monkeypatch, app)

    spec = RemoteSpec(url="http://test")
    result = await remote_schedule_push(
        spec, name="foo", spec_body={"workflow": "prompt"},
        pushed_from="peer:zion")
    assert result["name"] == "foo"
    assert result["written_to"].endswith("foo.yaml")
    assert captured["header"] == "peer:zion"
    assert captured["body"] == {"workflow": "prompt"}


@pytest.mark.asyncio
async def test_remote_schedule_push_error_normalized(monkeypatch):
    async def handler(_: Request) -> JSONResponse:
        return JSONResponse({"error": "bad spec"}, status_code=400)

    app = Starlette(routes=[
        Route("/remote/v1/schedule/{name}", handler, methods=["PUT"])])
    _wire(monkeypatch, app)

    result = await remote_schedule_push(
        RemoteSpec(url="http://test"), name="x",
        spec_body={}, pushed_from="peer:zion")
    assert "error" in result
    assert "schedule push" in result["error"]
    assert "400" in result["error"]
    assert "bad spec" in result["error"]


@pytest.mark.asyncio
async def test_remote_schedule_list(monkeypatch):
    rows = [{"name": "a", "source": "pushed"},
            {"name": "b", "source": "inline"}]

    async def handler(_: Request) -> JSONResponse:
        return JSONResponse({"schedules": rows})

    app = Starlette(routes=[
        Route("/remote/v1/schedule", handler, methods=["GET"])])
    _wire(monkeypatch, app)

    result = await remote_schedule_list(RemoteSpec(url="http://test"))
    assert result == {"schedules": rows}


@pytest.mark.asyncio
async def test_remote_schedule_show(monkeypatch):
    async def handler(request: Request) -> JSONResponse:
        return JSONResponse({"name": request.path_params["name"],
                             "source": "pushed",
                             "spec": {"workflow": "prompt"}})

    app = Starlette(routes=[
        Route("/remote/v1/schedule/{name}", handler, methods=["GET"])])
    _wire(monkeypatch, app)

    result = await remote_schedule_show(RemoteSpec(url="http://test"), "foo")
    assert result["name"] == "foo"
    assert result["source"] == "pushed"


@pytest.mark.asyncio
async def test_remote_schedule_remove(monkeypatch):
    captured: dict = {}

    async def handler(request: Request) -> Response:
        captured["name"] = request.path_params["name"]
        return Response(status_code=204)

    app = Starlette(routes=[
        Route("/remote/v1/schedule/{name}", handler, methods=["DELETE"])])
    _wire(monkeypatch, app)

    result = await remote_schedule_remove(
        RemoteSpec(url="http://test"), "foo")
    assert result == {"ok": True}
    assert captured["name"] == "foo"


@pytest.mark.asyncio
async def test_remote_schedule_remove_not_found(monkeypatch):
    async def handler(_: Request) -> JSONResponse:
        return JSONResponse({"error": "not found"}, status_code=404)

    app = Starlette(routes=[
        Route("/remote/v1/schedule/{name}", handler, methods=["DELETE"])])
    _wire(monkeypatch, app)

    result = await remote_schedule_remove(
        RemoteSpec(url="http://test"), "ghost")
    assert "error" in result
    assert "404" in result["error"]
    assert "not found" in result["error"]


@pytest.mark.asyncio
async def test_remote_schedule_logs(monkeypatch):
    captured: dict = {}
    records = [{"event": "fire_completed", "i": 0}]

    async def handler(request: Request) -> JSONResponse:
        captured["tail"] = request.query_params.get("tail")
        captured["name"] = request.path_params["name"]
        return JSONResponse({"records": records})

    app = Starlette(routes=[
        Route("/remote/v1/schedule/{name}/logs", handler, methods=["GET"])])
    _wire(monkeypatch, app)

    result = await remote_schedule_logs(
        RemoteSpec(url="http://test"), "foo", tail=10)
    assert result == {"records": records}
    assert captured["tail"] == "10"
    assert captured["name"] == "foo"
