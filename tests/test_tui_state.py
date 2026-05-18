from aegis.tui.state import AgentState


def test_three_states_exist():
    assert {s.name for s in AgentState} == {"ready", "working", "error"}


def test_dot_colors_are_distinct():
    dots = {s: s.dot for s in AgentState}
    assert "green" in dots[AgentState.ready]
    assert "yellow" in dots[AgentState.working] or "orange" in dots[AgentState.working]
    assert "red" in dots[AgentState.error]
    assert "●" in dots[AgentState.ready]


def test_labels():
    assert AgentState.ready.label == "idle"
    assert "working" in AgentState.working.label
    assert AgentState.error.label.startswith("⚠")
