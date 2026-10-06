"""write_target — which tool calls promote a repo onto the list."""
from __future__ import annotations

import pytest

from aegis.repos.writes import write_target


@pytest.mark.parametrize("name", ["Write", "Edit", "MultiEdit"])
def test_the_claude_write_tools_report_their_file(name):
    assert write_target(name, {"file_path": "/w/repos/aegis/x.py"}) \
        == "/w/repos/aegis/x.py"


def test_notebook_edit_reports_its_notebook():
    assert write_target("NotebookEdit",
                        {"notebook_path": "/w/nb.ipynb"}) == "/w/nb.ipynb"


@pytest.mark.parametrize("name,inp", [
    ("Read", {"file_path": "/w/repos/aegis/x.py"}),
    ("Grep", {"path": "/w/repos/aegis"}),
    ("Glob", {"path": "/w/repos/aegis"}),
    ("WebFetch", {"url": "https://example.com"}),
])
def test_reads_and_searches_promote_nothing(name, inp):
    assert write_target(name, inp) is None


def test_bash_promotes_nothing_even_when_it_clearly_writes():
    """Deliberate. Guessing write targets out of a shell command is the
    heuristic the mandatory-claims spec refused to pretend was complete."""
    assert write_target("Bash", {"command": "sed -i s/a/b/ repos/aegis/x.py"}) \
        is None
    assert write_target("Bash", {"command": "echo hi > repos/aegis/x.py"}) \
        is None


def test_an_acp_edit_is_recognised_by_kind_not_by_title():
    """Every ACP harness titles its tools differently; the kind is the
    stable thing."""
    assert write_target("apply_patch", {"path": "/w/x.py"}, kind="edit") \
        == "/w/x.py"
    assert write_target("edit_file", {}, locations=(("/w/y.py", 3),),
                        kind="edit") == "/w/y.py"


def test_an_acp_read_promotes_nothing():
    assert write_target("read_file", {"path": "/w/x.py"}, kind="read") is None


def test_a_write_with_no_path_anywhere_is_none():
    assert write_target("Write", {}) is None
    assert write_target("Write", None) is None


def test_an_empty_path_is_not_a_target():
    assert write_target("Write", {"file_path": ""}) is None
