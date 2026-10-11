"""The quota gate in the real app: the fake claude, a stub reading, a member."""

import time

import pytest

from aegis.ops import Caller, Principal

from .test_agents import CONFIG, World

BEN = Principal("ben", "member")
LINES = {"claude": {"mode": "lines", "lines": {"session": {"launch": 50, "stop": 90}}}}


def seed(world, session=40.0, weekly=10.0, projected=None, read_at=None):
    """Make every reading the app takes say ``session``% of Claude's 5 hours."""
    now = time.time()

    def snapshot():
        return {
            "providers": [
                {
                    "name": "claude",
                    "label": "Claude",
                    "harness": "claude-code",
                    "state": "ok",
                    "read_at": now - 30 if read_at is None else read_at,
                    "windows": [
                        {
                            "kind": "session",
                            "label": "5 hours",
                            "percent": session,
                            "projected": projected,
                            "resets_at": now + 3600,
                        },
                        {
                            "kind": "weekly_all",
                            "label": "week",
                            "percent": weekly,
                            "projected": None,
                            "resets_at": now + 86400,
                        },
                    ],
                }
            ]
        }

    world.app.quota.snapshot = snapshot
    world.app.quota.check()


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    w.app.principals.add("ben", "member")
    await w.app.registry.call(
        "token.policy", {"name": "ben", "policy": LINES}, Caller("user")
    )
    yield w
    await w.stop()


def as_(principal):
    return Caller("user", principal=principal)


async def test_the_gate_follows_the_reading(world):
    app = world.app
    for pct, state in [(40, "open"), (63, "closed"), (91, "stopped"), (40, "open")]:
        seed(world, session=pct)
        assert app.gate("ben", "claude-code", "opus").state == state, pct
        assert app.gate(app.user, "claude-code", "opus").state == "open"
        assert app._gates[("ben", "claude")] == state
