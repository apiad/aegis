from __future__ import annotations

import textwrap
from pathlib import Path

from aegis.web.subscriptions import SubscriptionRegistry


def _cfg(root: Path) -> None:
    (root / ".aegis.yaml").write_text(textwrap.dedent("""
        default_agent: opus
        agents:
          opus:
            provider: claude-code
            model: opus
            effort: high
            permission: full
    """), encoding="utf-8")


def _reg(root: Path) -> SubscriptionRegistry:
    _cfg(root)
    reg = SubscriptionRegistry(object(), root / "state")
    reg.set_files(None, root.resolve())
    return reg


async def test_add_and_remove_agent(tmp_path: Path):
    reg = _reg(tmp_path)
    r = await reg.config_add_agent(
        "sonnet", provider="claude-code", model="sonnet", effort="high")
    assert r.get("ok")
    assert "sonnet" in [a["slug"] for a in reg.config_show()["agents"]]
    r2 = await reg.config_remove_agent("sonnet")
    assert r2.get("ok")
    assert "sonnet" not in [a["slug"] for a in reg.config_show()["agents"]]


async def test_add_agent_duplicate_errors(tmp_path: Path):
    reg = _reg(tmp_path)
    r = await reg.config_add_agent("opus", provider="claude-code", model="opus")
    assert "error" in r


async def test_add_agent_bad_provider_errors(tmp_path: Path):
    reg = _reg(tmp_path)
    r = await reg.config_add_agent("x", provider="bogus", model="m")
    assert "error" in r


async def test_add_and_remove_queue(tmp_path: Path):
    reg = _reg(tmp_path)
    r = await reg.config_add_queue("build", agent="opus", max_parallel=2)
    assert r.get("ok")
    assert "build" in [q["name"] for q in reg.config_show()["queues"]]
    r2 = await reg.config_remove_queue("build")
    assert r2.get("ok")
    assert "build" not in [q["name"] for q in reg.config_show()["queues"]]
