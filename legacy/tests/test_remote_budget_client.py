import httpx
import pytest

from aegis.remote.client import remote_budget_list, remote_budget_show
from aegis.remote.config import RemoteSpec


@pytest.mark.asyncio
async def test_remote_budget_list(httpx_mock):
    spec = RemoteSpec(url="http://1.2.3.4:8556")
    httpx_mock.add_response(
        method="GET", url="http://1.2.3.4:8556/remote/v1/budget",
        status_code=200,
        json={"queues": [{"name": "impl", "budgets_count": 1,
                           "status": "ok"}]})
    r = await remote_budget_list(spec)
    assert r["queues"][0]["name"] == "impl"


@pytest.mark.asyncio
async def test_remote_budget_show(httpx_mock):
    spec = RemoteSpec(url="http://1.2.3.4:8556")
    httpx_mock.add_response(
        method="GET", url="http://1.2.3.4:8556/remote/v1/budget/impl",
        status_code=200,
        json={"name": "impl", "allowed": True, "checks": [],
              "blocked_by": [], "unblock_at": None})
    r = await remote_budget_show(spec, "impl")
    assert r["name"] == "impl"


@pytest.mark.asyncio
async def test_remote_budget_show_404(httpx_mock):
    spec = RemoteSpec(url="http://1.2.3.4:8556")
    httpx_mock.add_response(
        method="GET", url="http://1.2.3.4:8556/remote/v1/budget/ghost",
        status_code=404,
        json={"error": "unknown queue"})
    r = await remote_budget_show(spec, "ghost")
    assert "error" in r
