from unittest.mock import MagicMock

import pytest

from aegis.queue.manager import QueueManager
from aegis.queue.schema import Queue


def _q(name: str, max_parallel: int = 1) -> Queue:
    return Queue(name=name, agent_profile="researcher",
                 max_parallel=max_parallel,
                 provider="claude-code", model="opus", budgets=[])


def test_register_queue_adds_to_live_map_and_state():
    qm = QueueManager({}, session_manager=MagicMock(),
                      inbox_router=MagicMock())
    qm.register_queue(_q("designs"))
    assert "designs" in qm._queues
    assert qm._pending["designs"] == []
    assert qm._inflight["designs"] == []
    assert "designs" in qm.list_queues()


def test_register_queue_idempotent_on_identical():
    qm = QueueManager({"designs": _q("designs")},
                      session_manager=MagicMock(),
                      inbox_router=MagicMock())
    qm.register_queue(_q("designs"))  # no raise


def test_register_queue_rejects_collision_with_different_spec():
    qm = QueueManager({"designs": _q("designs", max_parallel=1)},
                      session_manager=MagicMock(),
                      inbox_router=MagicMock())
    with pytest.raises(ValueError, match="already registered"):
        qm.register_queue(_q("designs", max_parallel=5))
