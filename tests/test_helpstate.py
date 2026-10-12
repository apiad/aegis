"""The per-principal help record (helpstate.py): the new-person rule, the
marks, write-then-rename, and a damaged file."""

import json
from importlib.metadata import version

import pytest

from aegis.helpstate import HelpState, installed, newer


def test_a_principal_who_owns_no_session_is_new_and_starts_toured(tmp_path):
    st = HelpState(tmp_path / "help")
    rec = st.get("ana", owns_sessions=False, installed="2.6.0")
    assert rec == {"tutorial": False, "toured": "2.6.0"}
    assert json.loads((tmp_path / "help" / "ana.json").read_text()) == rec


def test_a_principal_who_owns_sessions_was_here_before_and_toured_nothing(tmp_path):
    st = HelpState(tmp_path / "help")
    rec = st.get("ben", owns_sessions=True, installed="2.6.0")
    assert rec == {"tutorial": False, "toured": None}


def test_the_decision_is_made_once(tmp_path):
    st = HelpState(tmp_path / "help")
    st.get("ana", owns_sessions=False, installed="2.6.0")
    # Her first session must not turn her into someone who was here before.
    assert st.get("ana", owns_sessions=True, installed="2.7.0")["toured"] == "2.6.0"


def test_marks_persist_and_keep_the_other_field(tmp_path):
    st = HelpState(tmp_path / "help")
    st.get("ben", owns_sessions=True, installed="2.6.0")
    st.mark_tutorial("ben")
    st.mark_toured("ben", "2.6.0")
    again = HelpState(tmp_path / "help")
    assert again.get("ben", owns_sessions=True, installed="2.6.0") == {
        "tutorial": True,
        "toured": "2.6.0",
    }


def test_a_write_leaves_no_temporary_file(tmp_path):
    st = HelpState(tmp_path / "help")
    st.get("ana", owns_sessions=False, installed="2.6.0")
    st.mark_tutorial("ana")
    assert sorted(p.name for p in (tmp_path / "help").iterdir()) == ["ana.json"]


@pytest.mark.parametrize(
    "text",
    [
        "{not json",
        "[]",
        '{"tutorial": "yes", "toured": null}',
        '{"tutorial": true, "toured": 2}',
    ],
)
def test_a_damaged_record_is_decided_again_and_rewritten(tmp_path, text):
    d = tmp_path / "help"
    d.mkdir()
    (d / "ben.json").write_text(text)
    st = HelpState(d)
    assert st.get("ben", owns_sessions=True, installed="2.6.0") == {
        "tutorial": False,
        "toured": None,
    }
    assert json.loads((d / "ben.json").read_text()) == {
        "tutorial": False,
        "toured": None,
    }


def test_newer_compares_releases_as_numbers():
    assert newer("2.6.0", None)
    assert not newer("2.6.0", "2.6.0")
    assert newer("2.10.0", "2.9.4")
    assert not newer("2.6.0", "2.7.0")


def test_installed_is_this_packages_release():
    assert installed() == version("aegis-harness")
