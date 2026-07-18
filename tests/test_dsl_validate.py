from __future__ import annotations

import pytest

from aegis.dsl.models import Spec
from aegis.dsl.validate import DslValidationError, validate

AGENTS = {"worker", "lister", "merger"}


def _v(spec_dict, **kw):
    kw.setdefault("agents", AGENTS)
    kw.setdefault("queues", set())
    kw.setdefault("default_agent", "worker")
    validate(Spec.model_validate(spec_dict), **kw)


def test_valid_upstream_reference_passes():
    _v({"meta": {"name": "ok"}, "root": {"type": "sequence", "children": [
        {"type": "agent", "id": "list", "prompt": "p",
         "target": {"kind": "spawn", "profile": "lister"}},
        {"type": "agent", "id": "r", "prompt": "{{a}}", "inputs": {"a": "list"},
         "target": {"kind": "spawn", "profile": "merger"}}]}})


def test_forward_reference_rejected():
    with pytest.raises(DslValidationError):
        _v({"meta": {"name": "bad"}, "root": {"type": "sequence", "children": [
            {"type": "agent", "id": "r", "prompt": "{{a}}", "inputs": {"a": "later"},
             "target": {"kind": "spawn", "profile": "merger"}},
            {"type": "agent", "id": "later", "prompt": "p",
             "target": {"kind": "spawn", "profile": "lister"}}]}})


def test_id_collision_rejected():
    with pytest.raises(DslValidationError):
        _v({"meta": {"name": "bad"}, "root": {"type": "sequence", "children": [
            {"type": "agent", "id": "dup", "prompt": "p",
             "target": {"kind": "spawn", "profile": "worker"}},
            {"type": "agent", "id": "dup", "prompt": "p",
             "target": {"kind": "spawn", "profile": "worker"}}]}})


def test_unknown_profile_rejected():
    with pytest.raises(DslValidationError):
        _v({"meta": {"name": "bad"},
            "root": {"type": "agent", "id": "x", "prompt": "p",
                     "target": {"kind": "spawn", "profile": "ghost"}}})


def test_missing_target_without_default_rejected():
    with pytest.raises(DslValidationError):
        _v({"meta": {"name": "bad"},
            "root": {"type": "agent", "id": "x", "prompt": "p"}},
           default_agent=None)


def test_map_over_upstream_ok():
    _v({"meta": {"name": "s"}, "root": {"type": "sequence", "children": [
        {"type": "agent", "id": "list", "prompt": "p",
         "target": {"kind": "spawn", "profile": "lister"}},
        {"type": "map", "id": "audits", "over": "list.files",
         "body": {"type": "agent", "prompt": "{{item}}",
                  "target": {"kind": "spawn", "profile": "worker"}}}]}})


def test_map_over_downstream_rejected():
    with pytest.raises(DslValidationError):
        _v({"meta": {"name": "s"}, "root": {"type": "sequence", "children": [
            {"type": "map", "id": "audits", "over": "later.files",
             "body": {"type": "agent", "prompt": "{{item}}",
                      "target": {"kind": "spawn", "profile": "worker"}}},
            {"type": "agent", "id": "later", "prompt": "p",
             "target": {"kind": "spawn", "profile": "lister"}}]}})


def test_map_body_item_index_not_treated_as_ref():
    # `{{item}}` in a body input selector would be an error; but the
    # validator treats item/index as reserved scope names.
    _v({"meta": {"name": "s"}, "root": {"type": "sequence", "children": [
        {"type": "agent", "id": "src", "prompt": "p",
         "target": {"kind": "spawn", "profile": "lister"}},
        {"type": "map", "id": "m", "over": "src.files",
         "body": {"type": "agent", "prompt": "{{x}}",
                  "inputs": {"x": "item"},
                  "target": {"kind": "spawn", "profile": "worker"}}}]}})


def test_parallel_children_recursion_finds_bad_ref():
    with pytest.raises(DslValidationError):
        _v({"meta": {"name": "s"}, "root": {"type": "parallel", "children": [
            {"type": "agent", "id": "x", "prompt": "{{a}}",
             "inputs": {"a": "nope"},
             "target": {"kind": "spawn", "profile": "worker"}}]}})
