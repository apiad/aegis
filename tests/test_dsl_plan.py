from __future__ import annotations

from aegis.dsl.models import Spec
from aegis.dsl.plan import build_plan


def test_static_sequence_exact_count():
    spec = Spec.model_validate({"meta": {"name": "s"},
        "root": {"type": "sequence", "children": [
            {"type": "agent", "id": "a", "prompt": "p",
             "target": {"kind": "spawn", "profile": "w"}},
            {"type": "agent", "id": "b", "prompt": "p",
             "target": {"kind": "spawn", "profile": "w"}}]}})
    plan = build_plan(spec)
    assert plan.projected_agents == 2
    assert plan.is_upper_bound is False


def test_loop_uses_max_rounds_upper_bound():
    spec = Spec.model_validate({"meta": {"name": "s"},
        "root": {"type": "loop", "id": "r", "max_rounds": 4,
                 "until": {"kind": "shell", "cmd": "true"},
                 "body": {"type": "agent", "prompt": "p",
                          "target": {"kind": "spawn", "profile": "w"}}}})
    plan = build_plan(spec)
    assert plan.projected_agents == 4
    assert plan.is_upper_bound is True
    assert "upper bound" in plan.render()


def test_judge_predicate_counts_as_agent():
    spec = Spec.model_validate({"meta": {"name": "s"},
        "root": {"type": "if",
                 "cond": {"kind": "judge", "condition": "ok?", "inputs": []},
                 "then": {"type": "agent", "prompt": "p",
                          "target": {"kind": "spawn", "profile": "w"}}}})
    plan = build_plan(spec)
    assert plan.projected_agents == 2


def test_map_upper_bound():
    spec = Spec.model_validate({"meta": {"name": "s"},
        "root": {"type": "map", "id": "m", "over": "x.files",
                 "body": {"type": "agent", "prompt": "p",
                          "target": {"kind": "spawn", "profile": "w"}}}})
    plan = build_plan(spec)
    assert plan.projected_agents == 1
    assert plan.is_upper_bound is True


def test_human_node_contributes_zero_agents():
    spec = Spec.model_validate({"meta": {"name": "s"},
        "root": {"type": "human", "id": "g", "question": "?",
                 "schema": {"type": "string", "enum": ["a", "b"]}}})
    plan = build_plan(spec)
    assert plan.projected_agents == 0
    assert plan.is_upper_bound is False
