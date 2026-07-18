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
