from aegis.tui.groups.dashboard import (
    BroadcastRow,
    DashboardSnapshot,
    MemberRow,
    render_dashboard,
    state_glyph,
)


def test_dashboard_renders_three_panels():
    snap = DashboardSnapshot(
        name="reviewers",
        members=[
            MemberRow("ada", "idle", "last turn 18s · 1.2k tok"),
            MemberRow("lucid", "busy", "tool: Read foo.py · 04:12"),
        ],
        current=BroadcastRow("br-9f3a", "wait_all", "⏳",
                             "14:30 (02:18 ago)",
                             "Audit branch feat/auth for security regressions."),
        recent=[BroadcastRow("br-7c11", "wait_all", "✓",
                             "14:25", "3/3 in 01:42 · concat")],
    )
    out = render_dashboard(snap)
    assert "▣ reviewers — 2 members" in out
    assert "ada" in out and "lucid" in out
    assert "br-9f3a" in out
    assert "br-7c11" in out
    assert "Members" in out
    assert "Current broadcast" in out
    assert "Recent broadcasts" in out


def test_dashboard_empty_panels_show_placeholders():
    snap = DashboardSnapshot(name="empty")
    out = render_dashboard(snap)
    assert "(no members)" in out
    assert "(no broadcast in flight)" in out
    assert "(no broadcasts yet)" in out


def test_state_glyph_map():
    assert state_glyph("idle") == "✓"
    assert state_glyph("busy") == "⏳"
    assert state_glyph("errored") == "⚠"
    assert state_glyph("lost") == "⛔"
    assert state_glyph("anything-else") == "?"
