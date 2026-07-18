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
