from __future__ import annotations


def gate_decision(*, projected_agents: int, threshold: int,
                  operator_invoked: bool) -> str:
    if operator_invoked:
        return "auto"
    return "auto" if projected_agents <= threshold else "prompt"
