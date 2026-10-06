from __future__ import annotations

import pytest
from pydantic import ValidationError

from aegis.dsl.models import Spec


def test_parse_minimal_sequence_of_agents():
    spec = Spec.model_validate({
        "meta": {"name": "s1", "description": "seq of agents"},
        "root": {
            "type": "sequence",
            "children": [
                {"type": "agent", "id": "a",
                 "prompt": "do a",
                 "target": {"kind": "spawn", "profile": "worker"}},
                {"type": "agent", "id": "b", "prompt": "do b",
                 "target": {"kind": "spawn", "profile": "worker"}},
            ],
        },
    })
    assert spec.meta.name == "s1"
    assert spec.root.type == "sequence"
    assert spec.root.children[0].type == "agent"
    assert spec.root.children[0].target.profile == "worker"


def test_unknown_node_type_rejected():
    with pytest.raises(ValidationError):
        Spec.model_validate({
            "meta": {"name": "bad"},
            "root": {"type": "frobnicate", "children": []},
        })


def test_agent_requires_prompt():
    with pytest.raises(ValidationError):
        Spec.model_validate({
            "meta": {"name": "bad"},
            "root": {"type": "agent",
                     "target": {"kind": "spawn", "profile": "w"}},
        })


def test_agent_inputs_and_schema_parse():
    spec = Spec.model_validate({
        "meta": {"name": "s"},
        "root": {"type": "agent", "id": "r", "prompt": "merge {{all}}",
                 "inputs": {"all": "audits"},
                 "schema": {"type": "object",
                            "properties": {"x": {"type": "string"}}}},
    })
    assert spec.root.inputs == {"all": "audits"}
    assert spec.root.schema_["type"] == "object"


def test_agent_invalid_json_schema_rejected():
    with pytest.raises(ValidationError):
        Spec.model_validate({
            "meta": {"name": "s"},
            "root": {"type": "agent", "prompt": "x",
                     "schema": {"type": "not-a-real-type"}}})


def test_map_requires_id_and_over():
    from aegis.dsl.models import Spec
    with pytest.raises(ValidationError):
        Spec.model_validate({"meta": {"name": "s"},
            "root": {"type": "map", "over": "list.files",
                     "body": {"type": "agent", "prompt": "p",
                              "target": {"kind": "spawn", "profile": "w"}}}})


def test_parallel_and_map_parse():
    from aegis.dsl.models import Spec
    spec = Spec.model_validate({"meta": {"name": "s"},
        "root": {"type": "map", "id": "audits", "over": "list.files",
                 "concurrency": 4,
                 "body": {"type": "parallel", "children": [
                     {"type": "agent", "prompt": "{{item}}",
                      "target": {"kind": "spawn", "profile": "w"}}]}}})
    assert spec.root.type == "map"
    assert spec.root.over == "list.files"
    assert spec.root.concurrency == 4
    assert spec.root.body.type == "parallel"


def test_loop_requires_max_rounds():
    with pytest.raises(ValidationError):
        Spec.model_validate({"meta": {"name": "s"},
            "root": {"type": "loop", "id": "r",
                     "until": {"kind": "shell", "cmd": "true"},
                     "body": {"type": "agent", "prompt": "p",
                              "target": {"kind": "spawn", "profile": "w"}}}})


def test_loop_zero_max_rounds_rejected():
    with pytest.raises(ValidationError):
        Spec.model_validate({"meta": {"name": "s"},
            "root": {"type": "loop", "id": "r", "max_rounds": 0,
                     "until": {"kind": "shell", "cmd": "true"},
                     "body": {"type": "agent", "prompt": "p",
                              "target": {"kind": "spawn", "profile": "w"}}}})


def test_if_with_else_alias_parses():
    spec = Spec.model_validate({"meta": {"name": "s"},
        "root": {"type": "if",
                 "cond": {"kind": "shell", "cmd": "test -f x"},
                 "then": {"type": "agent", "prompt": "y",
                          "target": {"kind": "spawn", "profile": "w"}},
                 "else": {"type": "agent", "prompt": "n",
                          "target": {"kind": "spawn", "profile": "w"}}}})
    assert spec.root.type == "if"
    assert spec.root.then.prompt == "y"
    assert spec.root.else_.prompt == "n"


def test_human_node_with_enum_schema_parses():
    spec = Spec.model_validate({"meta": {"name": "s"},
        "root": {"type": "human", "id": "gate1",
                 "question": "Proceed?",
                 "schema": {"type": "string",
                            "enum": ["proceed", "revise"]}}})
    assert spec.root.type == "human"
    assert spec.root.question == "Proceed?"
    assert spec.root.schema_["enum"] == ["proceed", "revise"]


def test_unknown_predicate_kind_rejected():
    with pytest.raises(ValidationError):
        Spec.model_validate({"meta": {"name": "s"},
            "root": {"type": "if",
                     "cond": {"kind": "frobnicate"},
                     "then": {"type": "agent", "prompt": "p",
                              "target": {"kind": "spawn", "profile": "w"}}}})
