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
    c, _ = config(
        tmp_path, "scheduler: {tick_seconds: 5}\nvoice: {preview: true}\n" + A
    )
    assert c.current().unknown_keys == ("scheduler", "voice")


def test_the_doc_keeps_a_broken_queue_editable(tmp_path):
    d = doc_from(
        {"queues": {"broken": {"agent": "a"}, "ok": {"agent": "a", "max_parallel": 2}}}
    )
    assert d.queues == [
        QueueDoc(name="broken", agent="a", max_parallel=None),
        QueueDoc(name="ok", agent="a", max_parallel=2),
    ]


def test_the_wire_carries_the_doc_the_stamp_and_the_vocabulary(tmp_path):
    c, _ = config(tmp_path, A)
    w = c.current().wire()
    assert w["exists"] and w["error"] is None and w["stamp"].count(":") == 2
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


def test_context_defaults_when_absent():
    from aegis.config import CONTEXT_DEFAULTS, context_from

    assert (
        context_from(None)
        == CONTEXT_DEFAULTS
        == {
            "handoff_at": 300000,
            "cold_after": 3600,
            "cold_min": 100000,
        }
    )


def test_context_overrides_one_key_and_rides_the_snapshot(tmp_path):
    from aegis.config import context_from

    assert context_from({"handoff_at": 0})["handoff_at"] == 0
    snap = Snapshot.parse(tmp_path, None, {"context": {"cold_min": 5}})
    assert snap.context["cold_min"] == 5 and snap.unknown_keys == ()


def test_a_bad_context_keeps_the_defaults_and_says_why():
    from aegis.config import CONTEXT_DEFAULTS, context_from

    for raw, word in (
        ({"handof_at": 1}, "handof_at"),
        ({"cold_min": -1}, "cold_min"),
        ({"cold_after": 0}, "cold_after"),
        ({"cold_min": True}, "cold_min"),
        ("big", "mapping"),
    ):
        c = context_from(raw)
        assert word in c["error"]
        assert {k: c[k] for k in CONTEXT_DEFAULTS} == CONTEXT_DEFAULTS


def test_the_workflows_block_sets_the_pool_and_rides_the_snapshot(tmp_path):
    from aegis.config import WORKFLOWS_DEFAULTS, workflows_from

    assert workflows_from(None) == WORKFLOWS_DEFAULTS == {"max_agents": {"total": 4}}
    assert workflows_from({"max_agents": 2}) == {"max_agents": {"total": 2}}
    assert workflows_from({"max_agents": {"opus": 1}}) == {
        "max_agents": {"total": 4, "opus": 1}
    }
    raw = {"workflows": {"max_agents": {"total": 10, "opus": 5}}}
    snap = Snapshot.parse(tmp_path, None, raw)
    assert snap.workflows == {"max_agents": {"total": 10, "opus": 5}}
    assert snap.unknown_keys == ()


def test_a_bad_workflows_block_keeps_the_default_and_says_why():
    from aegis.config import WORKFLOWS_DEFAULTS, workflows_from
    from aegis.doctor import _workflows

    for raw, word in (
        ({"max_agent": 2}, "max_agent"),
        ({"max_agents": 0}, "max_agents"),
        ({"max_agents": True}, "max_agents"),
        ({"max_agents": {"total": 2, "opus": 0}}, "max_agents.opus"),
        ("many", "mapping"),
    ):
        w = workflows_from(raw)
        assert word in w["error"]
        assert w["max_agents"] == WORKFLOWS_DEFAULTS["max_agents"]
        assert _workflows(w).level == "error"
    w = workflows_from({"max_agents": {"total": 3, "opus": 1}})
    assert _workflows(w, ("opus",)).level == "ok"
    bad = _workflows(workflows_from({"max_agents": {"opsu": 1}}), ("opus",))
    assert bad.level == "error" and "opsu" in bad.message


def test_the_doctor_flags_an_agents_number_above_the_total():
    """As the lint does a workflow's limits: the number could never bind."""
    from aegis.config import workflows_from
    from aegis.doctor import _workflows

    for raw in ({"total": 2, "opus": 3}, {"opus": 5}):  # 4 is the default total
        bad = _workflows(workflows_from({"max_agents": raw}), ("opus",))
        assert bad.level == "error", bad
        assert f"opus {raw['opus']} is above total" in bad.message, bad.message


def test_harness_versions_defaults_when_the_key_is_absent(tmp_path):
    c, _ = config(tmp_path, A)
    assert c.current().harness_versions == {
        "person": "ask",
        "agent": "warn",
        "startup": "warn",
        "accepted": {},
    }


def test_harness_versions_agent_ask_is_refused():
    from aegis.config import harness_versions_from

    got = harness_versions_from({"agent": "ask"})
    assert got["agent"] == "warn" and "nobody is there to answer" in got["error"]


def test_harness_versions_unknown_harness_under_accepted_is_an_error(tmp_path):
    from aegis.config import harness_versions_from

    got = harness_versions_from({"accepted": {"gemini": ["1.0"]}})
    assert "gemini" in got["error"] and got["accepted"] == {}
    c, _ = config(tmp_path, A + "harness_versions: {accepted: {gemini: ['1.0']}}\n")
    assert "error" in c.current().harness_versions
