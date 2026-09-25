"""The aegis-report block a worker must end with."""
from __future__ import annotations

import pytest

from aegis.workflows.builtins.afk.report import Report, ReportError, parse_report

GOOD = """
I did the thing. Here is my report.

```aegis-report
status: needs-review
summary: add the parser and its table tests
gate: make check -> 0
artifacts:
  - https://github.com/o/r/pull/9
  - 3f9a1c2
changed: 7
judgement:
  - the card did not say which of two behaviours; chose the first
notes: |
  the second behaviour is one line away if you want it
```
"""


def test_parses_a_well_formed_report() -> None:
    r = parse_report(GOOD)
    assert r.status == "needs-review"
    assert r.summary == "add the parser and its table tests"
    assert r.gate_cmd == "make check"
    assert r.gate_exit == 0
    assert r.artifacts == ("https://github.com/o/r/pull/9", "3f9a1c2")
    assert r.changed == 7
    assert len(r.judgement) == 1
    assert "one line away" in r.notes


def test_absent_block_raises() -> None:
    with pytest.raises(ReportError, match="no aegis-report"):
        parse_report("I finished, trust me.")


def test_two_blocks_raise() -> None:
    """Two blocks means the worker quoted the format and then emitted one,
    or emitted two. Either way, which one is the report is a guess."""
    with pytest.raises(ReportError, match="2 aegis-report"):
        parse_report(GOOD + GOOD)


def test_unknown_status_raises() -> None:
    text = "```aegis-report\nstatus: done\nsummary: s\ngate: make check -> 0\n```"
    with pytest.raises(ReportError, match="status"):
        parse_report(text)


def test_missing_summary_raises() -> None:
    text = "```aegis-report\nstatus: failed\ngate: make check -> 1\n```"
    with pytest.raises(ReportError, match="summary"):
        parse_report(text)


def test_gate_none_is_allowed_and_leaves_exit_unset() -> None:
    """A repo declaring no gate target still runs, but its report must not
    read as a green gate."""
    text = "```aegis-report\nstatus: needs-review\nsummary: s\ngate: none\n```"
    r = parse_report(text)
    assert r.gate_cmd == "none"
    assert r.gate_exit is None


def test_unparseable_gate_line_raises() -> None:
    text = "```aegis-report\nstatus: needs-review\nsummary: s\ngate: it worked\n```"
    with pytest.raises(ReportError, match="gate"):
        parse_report(text)


def test_malformed_yaml_raises_reporterror_not_yamlerror() -> None:
    text = "```aegis-report\nstatus: [unclosed\n```"
    with pytest.raises(ReportError):
        parse_report(text)


def test_absent_optional_fields_default_empty() -> None:
    text = "```aegis-report\nstatus: blocked\nsummary: s\ngate: none\n```"
    r = parse_report(text)
    assert r.artifacts == ()
    assert r.judgement == ()
    assert r.changed is None
    assert r.notes == ""


def test_a_scalar_where_a_list_belongs_is_accepted() -> None:
    """Workers write `artifacts: some-url` about as often as they write a
    list. Coercing is kinder than failing a card over YAML shape."""
    text = (
        "```aegis-report\nstatus: needs-review\nsummary: s\n"
        "gate: make test -> 0\nartifacts: one-url\n```"
    )
    assert parse_report(text).artifacts == ("one-url",)
