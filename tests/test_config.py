import asyncio
from pathlib import Path

from aegis.config import Config, ConfigDoc, QueueDoc, Snapshot, doc_from

from .conftest import until

A = "default_agent: a\nagents:\n  a: {harness: claude-code, model: opus, effort: high, permission: full}\n"
AB = A + "  b: {harness: claude-code, model: sonnet, effort: low, permission: read}\n"


def config(tmp_path: Path, text: str | None = None) -> tuple[Config, list]:
    if text is not None:
        (tmp_path / ".aegis.yaml").write_text(text)
    seen: list[Snapshot] = []
    return Config(tmp_path, seen.append), seen


def names(s: Snapshot) -> list[str]:
    return [a.name for a in s.agents]


def test_a_change_on_disk_is_seen_without_a_tick(tmp_path):
    c, seen = config(tmp_path, A)
    assert names(c.current()) == ["a"] and seen == []
    (tmp_path / ".aegis.yaml").write_text(AB)
    assert names(c.current()) == ["a", "b"]
    assert len(seen) == 1
    c.current()
    assert len(seen) == 1, "an unchanged file fires nothing"


def test_a_parse_error_keeps_the_last_good_config(tmp_path):
    c, seen = config(tmp_path, A)
    (tmp_path / ".aegis.yaml").write_text("agents: [1, 2\n")
    s = c.current()
    assert names(s) == ["a"] and s.default_agent == "a"
    assert s.error and ".aegis.yaml" in s.error
    c.current()
    assert len(seen) == 1, "the same broken file is parsed once"
    (tmp_path / ".aegis.yaml").write_text(AB)
    assert names(c.current()) == ["a", "b"] and c.current().error is None


def test_an_empty_file_keeps_the_last_good_config(tmp_path):
    c, _ = config(tmp_path, A)
    (tmp_path / ".aegis.yaml").write_text("")
    s = c.current()
    assert names(s) == ["a"] and s.queues == {} and "empty" in s.error


def test_a_broken_file_at_start_has_no_agents_and_says_why(tmp_path):
    c, _ = config(tmp_path, "agents: [1, 2\n")
    s = c.current()
    assert s.exists and s.agents == () and s.error


def test_deleting_the_file_empties_the_config(tmp_path):
    c, seen = config(tmp_path, A)
    (tmp_path / ".aegis.yaml").unlink()
    s = c.current()
    assert not s.exists and s.agents == () and s.default_agent is None
    assert len(seen) == 1


def test_a_file_created_later_is_picked_up(tmp_path):
    c, _ = config(tmp_path)
    assert not c.current().exists
    (tmp_path / ".aegis.yaml").write_text(A)
    assert names(c.current()) == ["a"]


def test_unknown_top_level_keys_are_listed(tmp_path):
    c, _ = config(tmp_path, "scheduler: {tick_seconds: 5}\nvoice: {preview: true}\n" + A)
    assert c.current().unknown_keys == ("scheduler", "voice")


def test_the_doc_keeps_a_broken_queue_editable(tmp_path):
    d = doc_from({"queues": {"broken": {"agent": "a"}, "ok": {"agent": "a", "max_parallel": 2}}})
    assert d.queues == [
        QueueDoc(name="broken", agent="a", max_parallel=None),
        QueueDoc(name="ok", agent="a", max_parallel=2),
    ]


def test_the_wire_carries_the_doc_the_stamp_and_the_vocabulary(tmp_path):
    c, _ = config(tmp_path, A)
    w = c.current().wire()
    assert w["exists"] and w["error"] is None and len(w["stamp"]) == 3
    assert w["root"] == str(tmp_path)
    assert ConfigDoc.model_validate(w["doc"]).agents[0].name == "a"
    assert w["vocab"]["efforts"] == ["low", "medium", "high", "xhigh", "max"]


async def test_the_watch_loop_fires_once_per_change(tmp_path):
    c, seen = config(tmp_path, A)
    task = asyncio.create_task(c.watch(every=0.01))
    try:
        (tmp_path / ".aegis.yaml").write_text(AB)
        await until(lambda: len(seen) == 1, what="the change")
        await asyncio.sleep(0.05)
        assert len(seen) == 1
    finally:
        task.cancel()
