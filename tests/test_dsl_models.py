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
