"""The activity table and the purpose rules (spec 06), one row per line."""

import pytest

from aegis.usage.kinds import (
    activity,
    commit_purpose,
    direct_purpose,
    edit_purpose,
    is_shell_exit,
)


@pytest.mark.parametrize(
    "tool,inp,want",
    [
        ("Read", {}, "reading"),
        ("Grep", {}, "reading"),
        ("Glob", {}, "reading"),
        ("Edit", {}, "editing"),
        ("Write", {}, "editing"),
        ("NotebookEdit", {}, "editing"),
        ("Bash", {"command": "git commit -m 'fix: x'"}, "committing"),
        ("Bash", {"command": "git -C /w/atlas push"}, "committing"),
        ("Bash", {"command": "gh pr merge 3"}, "committing"),
        ("Bash", {"command": "gh pr create --title t"}, "committing"),
        ("Bash", {"command": "uv run pytest tests -q"}, "testing"),
        ("Bash", {"command": "cd /w/atlas && pytest -q"}, "testing"),
        (
            "Bash",
            {"command": "# Run the tests\nset -o pipefail; npm test | tail"},
            "testing",
        ),
        ("Bash", {"command": "gh pr checks 3"}, "testing"),
        ("Bash", {"command": "gh run view 9 --log-failed"}, "testing"),
        ("mcp__aegis__monitor_start", {}, "testing"),
        ("mcp__aegis__browser_click", {}, "testing"),
        ("Bash", {"command": "ssh host systemctl restart x"}, "server"),
        ("Bash", {"command": "sudo apt-get install -y jq"}, "server"),
        ("WebFetch", {}, "web"),
        ("WebSearch", {}, "web"),
        ("mcp__firecrawl__scrape", {}, "web"),
        ("Bash", {"command": "curl -s https://example.org"}, "web"),
        ("Bash", {"command": "curl -s localhost:8000"}, "reading"),
        ("Bash", {"command": "curl -s http://127.0.0.1:8000/x"}, "reading"),
        ("Bash", {"command": "gh pr diff 3"}, "reading"),
        ("Bash", {"command": "git log --oneline -5"}, "reading"),
        ("mcp__aegis__queue_enqueue", {}, "other agents"),
        ("mcp__aegis__peer_handoff", {}, "other agents"),
        ("mcp__aegis__session_spawn", {}, "other agents"),
        ("Task", {}, "other agents"),
        ("mcp__aegis__file_send", {}, "showing"),
        ("mcp__aegis__artifact_send", {}, "showing"),
        ("mcp__aegis__toast", {}, "showing"),
        ("mcp__aegis__turn_end", {}, "bookkeeping"),
        ("mcp__aegis__help_search", {}, "bookkeeping"),
        ("mcp__aegis__journal_search", {}, "bookkeeping"),
        ("ToolSearch", {}, "bookkeeping"),
        ("Skill", {}, "bookkeeping"),
        ("Bash", {"command": "make build"}, "reading"),
    ],
)
def test_activity(tool, inp, want):
    assert activity(tool, inp) == want


def test_purposes():
    assert edit_purpose("/r/docs/superpowers/plans/x.html") == "planning"
    assert edit_purpose("/r/docs/superpowers/specs/x.html") == "planning"
    assert edit_purpose("/r/notes/cache-design.md") == "planning"
    assert edit_purpose("/r/README.md") == "docs"
    assert edit_purpose("/r/src/aegis/help/x.md") == "docs"
    assert edit_purpose("/r/know-how/a.md") == "docs"
    assert edit_purpose("/r/AGENTS.md") == "docs"
    assert edit_purpose("/r/book/ch1.tex") == "writing"
    assert edit_purpose("/r/src/a.py") is None
    assert commit_purpose("feat(x): y") == "build"
    assert commit_purpose("perf: y") == "build"
    assert commit_purpose("test: y") == "fix" and commit_purpose("fix!: y") == "fix"
    assert commit_purpose("style: y") == "refactor"
    assert commit_purpose("docs: y") == "docs"
    assert commit_purpose("ci: y") == "ops" and commit_purpose("chore: y") == "ops"
    assert commit_purpose("update things") is None
    assert direct_purpose("reading", "Bash", {"command": "gh pr view 3"}) == "review"
    assert direct_purpose("reading", "Bash", {"command": "git show HEAD"}) == "review"
    assert direct_purpose("reading", "Read", {}) is None
    assert direct_purpose("web", "WebSearch", {}) == "research"
    assert direct_purpose("server", "Bash", {"command": "ssh h"}) == "ops"
    assert direct_purpose("other agents", "Task", {}) == "coordinate"
    assert direct_purpose("testing", "Bash", {"command": "pytest"}) is None


def test_shell_exit_is_apart_from_other_errors():
    assert is_shell_exit("Bash", "Exit code 1\n1 failed", True)
    assert not is_shell_exit("Bash", "Exit code 1\n1 failed", False)
    assert not is_shell_exit("Read", "File does not exist.", True)
    assert not is_shell_exit("Bash", "Permission denied", True)
