"""Survey + repair of damaged session logs (`aegis doctor`)."""
from aegis.events import AssistantText, Result
from aegis.state.repair import repair_log, survey
from aegis.state.session_log import (
    append_event, replay_events, scan_log, session_log_path,
)


def _damage(tmp_path, handle: str, junk: str) -> None:
    with session_log_path(tmp_path, handle).open("a", encoding="utf-8") as f:
        f.write(junk + "\n")


def test_survey_reports_one_row_per_log(tmp_path):
    append_event(tmp_path, "clean", AssistantText(text="a", usage=None))
    append_event(tmp_path, "dirty", AssistantText(text="b", usage=None))
    _damage(tmp_path, "dirty", "\x00" * 50)

    rows = {r.handle: r for r in survey(tmp_path)}
    assert set(rows) == {"clean", "dirty"}
    assert rows["clean"].damaged == 0 and rows["clean"].healthy
    assert rows["dirty"].damaged == 1 and not rows["dirty"].healthy
    assert rows["dirty"].records == 1


def test_survey_of_empty_state_dir(tmp_path):
    assert survey(tmp_path) == []


def test_repair_rewrites_the_log_without_the_damage(tmp_path):
    h = "dirty"
    append_event(tmp_path, h, AssistantText(text="before", usage=None))
    _damage(tmp_path, h, "\x00" * 50)
    append_event(tmp_path, h, AssistantText(text="after", usage=None))
    append_event(tmp_path, h, Result(duration_ms=1, is_error=False))

    report = repair_log(session_log_path(tmp_path, h))
    assert report.damaged == 1
    assert report.records == 3

    after = scan_log(session_log_path(tmp_path, h))
    assert after.damaged == 0
    assert len(after.records) == 3
    assert [e.text for e in replay_events(tmp_path, h).events
            if isinstance(e, AssistantText)] == ["before", "after"]


def test_repair_keeps_the_original_as_a_backup(tmp_path):
    """Never destroy the only copy of a conversation to fix it — even the
    bytes we couldn't parse might be readable by hand later."""
    h = "dirty"
    append_event(tmp_path, h, AssistantText(text="one", usage=None))
    _damage(tmp_path, h, "\x00" * 50)
    original = session_log_path(tmp_path, h).read_bytes()

    report = repair_log(session_log_path(tmp_path, h))
    assert report.backup is not None
    assert report.backup.read_bytes() == original


def test_repair_preserves_a_salvaged_record(tmp_path):
    h = "dirty"
    append_event(tmp_path, h, AssistantText(text="buried", usage=None))
    p = session_log_path(tmp_path, h)
    p.write_text("\x00" * 300 + p.read_text(), encoding="utf-8")

    report = repair_log(p)
    assert report.recovered == 1
    assert [e.text for e in replay_events(tmp_path, h).events] == ["buried"]
    assert scan_log(p).damaged == 0


def test_repair_is_a_noop_on_a_clean_log(tmp_path):
    h = "clean"
    append_event(tmp_path, h, AssistantText(text="one", usage=None))
    before = session_log_path(tmp_path, h).read_bytes()

    report = repair_log(session_log_path(tmp_path, h))
    assert report.healthy
    assert report.backup is None
    assert session_log_path(tmp_path, h).read_bytes() == before


def test_repair_does_not_clobber_an_earlier_backup(tmp_path):
    h = "dirty"
    append_event(tmp_path, h, AssistantText(text="one", usage=None))
    _damage(tmp_path, h, "\x00" * 20)
    first = repair_log(session_log_path(tmp_path, h))
    _damage(tmp_path, h, "\x00" * 20)
    second = repair_log(session_log_path(tmp_path, h))
    assert first.backup != second.backup
    assert first.backup.exists() and second.backup.exists()


def test_survey_flags_logs_belonging_to_live_tabs(tmp_path):
    """Rewriting a log that a running session still holds an fd on would
    drop every event it appends afterwards — the repair has to know which
    handles are live."""
    from aegis.state.workspace import Workspace, WorkspaceTab, save
    append_event(tmp_path, "live", AssistantText(text="a", usage=None))
    _damage(tmp_path, "live", "\x00" * 20)
    append_event(tmp_path, "dead", AssistantText(text="b", usage=None))
    _damage(tmp_path, "dead", "\x00" * 20)
    save(tmp_path, Workspace(active_handle="live", tabs=[
        WorkspaceTab(handle="live", profile="p", order=0,
                     provider="claude-code", session_id="s",
                     created_at="2026-07-29T00:00:00Z")]))

    rows = {r.handle: r for r in survey(tmp_path)}
    assert rows["live"].live is True
    assert rows["dead"].live is False


# ---------- splitting logs that share a recycled handle ---------------


def _sess(tmp_path, handle, sid, text):
    from aegis.events import SystemInit
    append_event(tmp_path, handle, SystemInit(session_id=sid))
    append_event(tmp_path, handle, AssistantText(text=text, usage=None))
    append_event(tmp_path, handle, Result(duration_ms=1, is_error=False))


def test_split_separates_sessions_that_shared_a_handle(tmp_path):
    """Handles are recycled, so a legacy `<handle>.jsonl` can hold several
    unrelated conversations back to back — 100 of 223 on a real state dir,
    one of them five spanning 46 days. Each upstream session_id starts a new
    log under the new naming."""
    from aegis.state.repair import split_log
    _sess(tmp_path, "candid-cerf", "sid-may", "the may one")
    _sess(tmp_path, "candid-cerf", "sid-july", "the july one")

    parts = split_log(session_log_path(tmp_path, "candid-cerf"))
    assert len(parts) == 2
    texts = [
        [e.text for e in replay_events(tmp_path, p.stem).events
         if isinstance(e, AssistantText)]
        for p in parts
    ]
    assert texts == [["the may one"], ["the july one"]]
    # Each part keeps the handle and gains a birth time; parts born in the
    # same second get a disambiguating suffix rather than clobbering.
    assert all("candid-cerf" in p.stem for p in parts)
    assert parts[0].stem < parts[1].stem   # birth-time ordered


def test_split_keeps_the_original(tmp_path):
    from aegis.state.repair import split_log
    _sess(tmp_path, "h", "a", "one")
    _sess(tmp_path, "h", "b", "two")
    original = session_log_path(tmp_path, "h").read_bytes()
    split_log(session_log_path(tmp_path, "h"))
    backups = list((tmp_path / "sessions").glob("h.jsonl.split*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original
    assert not session_log_path(tmp_path, "h").exists()


def test_split_is_a_noop_for_a_single_session_log(tmp_path):
    from aegis.state.repair import split_log
    _sess(tmp_path, "h", "only", "one")
    before = session_log_path(tmp_path, "h").read_bytes()
    assert split_log(session_log_path(tmp_path, "h")) == []
    assert session_log_path(tmp_path, "h").read_bytes() == before


def test_split_keeps_records_that_precede_the_first_system_init(tmp_path):
    """Claude's SessionStart hooks stream before SystemInit, so the first
    session's opening records sit ahead of any boundary. They belong to it,
    not to nobody."""
    from aegis.events import Unknown
    from aegis.state.repair import split_log
    append_event(tmp_path, "h", Unknown(raw='{"subtype":"hook_started"}'))
    _sess(tmp_path, "h", "a", "one")
    _sess(tmp_path, "h", "b", "two")
    parts = split_log(session_log_path(tmp_path, "h"))
    first = replay_events(tmp_path, parts[0].stem).events
    assert isinstance(first[0], Unknown)


def test_split_survives_a_damaged_log(tmp_path):
    from aegis.state.repair import split_log
    _sess(tmp_path, "h", "a", "one")
    with session_log_path(tmp_path, "h").open("a", encoding="utf-8") as f:
        f.write("\x00" * 60 + "\n")
    _sess(tmp_path, "h", "b", "two")
    parts = split_log(session_log_path(tmp_path, "h"))
    assert len(parts) == 2
