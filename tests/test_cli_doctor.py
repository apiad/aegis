from typer.testing import CliRunner

from aegis.cli import app
from aegis.events import AssistantText
from aegis.state.session_log import append_event, scan_log, session_log_path
from aegis.state.workspace import (
    Workspace, WorkspaceTab, save, state_dir,
)

runner = CliRunner()


def _project(tmp_path):
    (tmp_path / ".aegis.yaml").write_text(
        "default_agent: default\n"
        "agents:\n"
        "  default:\n"
        "    provider: claude-code\n"
        "    model: opus\n"
    )
    return state_dir(tmp_path)


def _damaged_log(sd, handle: str):
    append_event(sd, handle, AssistantText(text="kept", usage=None))
    with session_log_path(sd, handle).open("a", encoding="utf-8") as f:
        f.write("\x00" * 40 + "\n")


def test_doctor_reports_damage_without_touching_it(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sd = _project(tmp_path)
    _damaged_log(sd, "broken")
    before = session_log_path(sd, "broken").read_bytes()

    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "broken" in result.output
    assert "1 damaged" in result.output
    assert session_log_path(sd, "broken").read_bytes() == before


def test_doctor_repair_rewrites_clean(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sd = _project(tmp_path)
    _damaged_log(sd, "broken")

    result = runner.invoke(app, ["doctor", "--repair"])
    assert result.exit_code == 0
    assert "repaired broken" in result.output
    assert scan_log(session_log_path(sd, "broken")).damaged == 0


def test_doctor_repair_skips_a_log_with_a_live_session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sd = _project(tmp_path)
    _damaged_log(sd, "open-tab")
    save(sd, Workspace(active_handle="open-tab", tabs=[
        WorkspaceTab(handle="open-tab", profile="default", order=0,
                     provider="claude-code", session_id="s",
                     created_at="2026-07-29T00:00:00Z")]))
    before = session_log_path(sd, "open-tab").read_bytes()

    result = runner.invoke(app, ["doctor", "--repair"])
    assert result.exit_code == 0
    assert "skipped open-tab" in result.output
    assert session_log_path(sd, "open-tab").read_bytes() == before


def _two_sessions(sd, handle):
    from aegis.events import SystemInit
    for sid, text in (("a", "first talk"), ("b", "second talk")):
        append_event(sd, handle, SystemInit(session_id=sid))
        append_event(sd, handle, AssistantText(text=text, usage=None))


def test_doctor_reports_merged_logs_without_touching_them(tmp_path,
                                                          monkeypatch):
    monkeypatch.chdir(tmp_path)
    sd = _project(tmp_path)
    _two_sessions(sd, "candid-cerf")
    before = session_log_path(sd, "candid-cerf").read_bytes()

    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "candid-cerf" in result.output
    assert "2 conversations sharing" in result.output
    assert "1 merged" in result.output
    assert session_log_path(sd, "candid-cerf").read_bytes() == before


def test_doctor_split_separates_them(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sd = _project(tmp_path)
    _two_sessions(sd, "candid-cerf")

    result = runner.invoke(app, ["doctor", "--split"])
    assert result.exit_code == 0
    assert "split candid-cerf" in result.output
    parts = sorted((sd / "sessions").glob("*-candid-cerf*.jsonl"))
    assert len(parts) == 2
    assert not session_log_path(sd, "candid-cerf").exists()


def test_doctor_split_skips_a_live_session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sd = _project(tmp_path)
    _two_sessions(sd, "open-tab")
    save(sd, Workspace(active_handle="open-tab", tabs=[
        WorkspaceTab(handle="open-tab", profile="default", order=0,
                     provider="claude-code", session_id="s",
                     created_at="2026-07-29T00:00:00Z")]))
    before = session_log_path(sd, "open-tab").read_bytes()

    result = runner.invoke(app, ["doctor", "--split"])
    assert result.exit_code == 0
    assert "skipped open-tab" in result.output
    assert session_log_path(sd, "open-tab").read_bytes() == before
