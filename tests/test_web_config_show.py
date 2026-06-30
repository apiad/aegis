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
        queues:
          work:
            agent: opus
            max_parallel: 2
        schedules:
          nightly:
            workflow: prompt
            cron: "0 2 * * *"
    """), encoding="utf-8")


def _reg(root: Path) -> SubscriptionRegistry:
    reg = SubscriptionRegistry(object(), root / "state")
    reg.set_files(None, root.resolve())   # files_root drives config root
    return reg


def test_config_show_lists_agents_queues_schedules(tmp_path: Path):
    _cfg(tmp_path)
    out = _reg(tmp_path).config_show()
    assert [a["slug"] for a in out["agents"]] == ["opus"]
    assert out["agents"][0]["model"] == "opus"
    assert [q["name"] for q in out["queues"]] == ["work"]
    assert out["queues"][0]["max_parallel"] == 2
    assert [s["name"] for s in out["schedules"]] == ["nightly"]
    assert out["schedules"][0]["cron"] == "0 2 * * *"


def test_config_show_no_config(tmp_path: Path):
    out = _reg(tmp_path).config_show()
    assert out == {"agents": [], "queues": [], "schedules": []}
