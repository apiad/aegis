from aegis.tui.state import AgentState


def test_three_states_exist():
    assert {s.name for s in AgentState} == {"ready", "working", "error"}


def test_dot_uses_supplied_colors():
    from aegis.tui.themes import aegis_colors, INK
    c = aegis_colors(INK)
    assert c.ready in AgentState.ready.dot(c)
    assert c.working in AgentState.working.dot(c)
    assert c.error in AgentState.error.dot(c)
    assert "●" in AgentState.ready.dot(c)


def test_labels():
    assert AgentState.ready.label == "idle"
    assert "working" in AgentState.working.label
    assert AgentState.error.label.startswith("⚠")
