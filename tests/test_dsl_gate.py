from __future__ import annotations

from pathlib import Path

from aegis.config.yaml_loader import load_config
from aegis.dsl.gate import gate_decision


def test_operator_invoked_always_auto():
    assert gate_decision(projected_agents=100, threshold=5,
                         operator_invoked=True) == "auto"


def test_agent_under_threshold_auto():
    assert gate_decision(projected_agents=5, threshold=5,
                         operator_invoked=False) == "auto"


def test_agent_over_threshold_prompts():
    assert gate_decision(projected_agents=6, threshold=5,
                         operator_invoked=False) == "prompt"


def test_config_reads_autoapprove_threshold(tmp_path: Path):
    (tmp_path / ".aegis.yaml").write_text(
        "dynamic_workflow_autoapprove_agents: 3\n")
    cfg = load_config(tmp_path)
    assert cfg.dynamic_workflow_autoapprove_agents == 3


def test_config_default_autoapprove_is_5(tmp_path: Path):
    (tmp_path / ".aegis.yaml").write_text("")
    cfg = load_config(tmp_path)
    assert cfg.dynamic_workflow_autoapprove_agents == 5
