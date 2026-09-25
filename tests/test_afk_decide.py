"""Quota, eligibility, ranking and review triggers — all pure."""

from __future__ import annotations

from aegis.workflows.builtins.afk.tick import FIELDS as _F

# Bound from the module rather than written out, so renaming the
# default field name cannot silently decouple these fixtures.
REPO_FIELD = _F["repo"]

import pytest

from aegis.usage.quota import QuotaSnapshot, QuotaState, QuotaWindow
from aegis.workflows.builtins.afk.board import Card
from aegis.workflows.builtins.afk.decide import (
    eligible,
    quota_gate,
    rank,
    resolve_repo_path,
    review_triggers,
)
from aegis.workflows.builtins.afk.report import Report

FIELDS = {
    "status": "Status",
    "repo": REPO_FIELD,
    "priority": "Priority",
    "deadline": "Deadline",
    "progress": "Progress",
    "waiting_on": "Waiting on",
}
STATUSES = {
    "todo": "Todo",
    "waiting": "Waiting",
    "running": "Running",
    "needs_review": "Needs review",
    "blocked": "Blocked",
    "failed": "Failed",
    "done": "Done",
}


def _state(*, weekly: float, session: float, failure: str = "") -> QuotaState:
    return QuotaState(
        snapshot=QuotaSnapshot(
            windows=(
                QuotaWindow("weekly_all", weekly, "normal", None, True),
                QuotaWindow("session", session, "normal", None, True),
            ),
            fetched_at=0.0,
        ),
        failure=failure,
    )


def _card(number, **fields):
    return Card(
        item_id=f"I{number}",
        number=number,
        repo="o/r",
        url=f"u/{number}",
        title=f"t{number}",
        body="b",
        state="OPEN",
        fields=fields,
    )


def test_quota_gate_allows_below_both_thresholds() -> None:
    g = quota_gate(_state(weekly=10, session=20), weekly_stop_at=60, session_stop_at=70)
    assert g.may_start is True


def test_quota_gate_stops_on_weekly() -> None:
    g = quota_gate(_state(weekly=61, session=5), weekly_stop_at=60, session_stop_at=70)
    assert g.may_start is False and "weekly" in g.reason


def test_quota_gate_stops_on_session() -> None:
    g = quota_gate(_state(weekly=5, session=70), weekly_stop_at=60, session_stop_at=70)
    assert g.may_start is False and "five-hour" in g.reason


def test_quota_gate_threshold_is_inclusive() -> None:
    """At exactly the threshold, stop. 'Stop at 60%' that starts at 60% is a
    threshold nobody configured."""
    assert (
        quota_gate(
            _state(weekly=60, session=0), weekly_stop_at=60, session_stop_at=70
        ).may_start
        is False
    )


def test_quota_gate_refuses_when_the_read_failed() -> None:
    """The Anthropic usage endpoint rate-limits in practice. 'I could not
    ask' is never read as 'there is room'."""
    g = quota_gate(None, weekly_stop_at=60, session_stop_at=70)
    assert g.may_start is False and "unread" in g.reason


def test_quota_gate_refuses_a_stale_reading() -> None:
    """A snapshot with a live failure is the last good reading, not a current
    one. Spending against it is spending blind."""
    g = quota_gate(
        _state(weekly=1, session=1, failure="rate_limited"),
        weekly_stop_at=60,
        session_stop_at=70,
    )
    assert g.may_start is False


def test_quota_gate_refuses_when_a_window_is_missing() -> None:
    state = QuotaState(snapshot=QuotaSnapshot(windows=(), fetched_at=0.0))
    assert quota_gate(state, weekly_stop_at=60, session_stop_at=70).may_start is False


def test_resolve_repo_path_accepts_a_whitelisted_name(tmp_path) -> None:
    (tmp_path / "aegis").mkdir()
    assert resolve_repo_path(tmp_path, "aegis") == (tmp_path / "aegis").resolve()


def test_resolve_repo_path_rejects_traversal(tmp_path) -> None:
    """A Repo value that escapes repo_root would dispatch a worker outside
    the whitelist. The single-select makes this hard to produce by hand and
    impossible to rely on."""
    (tmp_path / "aegis").mkdir()
    with pytest.raises(ValueError, match="outside"):
        resolve_repo_path(tmp_path, "../etc")


def test_resolve_repo_path_rejects_an_absolute_path(tmp_path) -> None:
    with pytest.raises(ValueError, match="outside"):
        resolve_repo_path(tmp_path, "/etc")


def test_resolve_repo_path_rejects_a_missing_directory(tmp_path) -> None:
    with pytest.raises(ValueError, match="no checkout"):
        resolve_repo_path(tmp_path, "not-cloned")


def test_resolve_repo_path_rejects_an_empty_repo_field(tmp_path) -> None:
    with pytest.raises(ValueError, match="no Repo"):
        resolve_repo_path(tmp_path, None)


def test_eligible_takes_todo_and_waiting(tmp_path) -> None:
    """Waiting must stay eligible: it is the coordinator's own deferral, and
    excluding it strands every card it ever deferred."""
    (tmp_path / "a").mkdir()
    cards = [
        _card(1, **{"Status": "Todo", REPO_FIELD: "a"}),
        _card(2, **{"Status": "Waiting", REPO_FIELD: "a"}),
        _card(3, **{"Status": "Running", REPO_FIELD: "a"}),
        _card(4, **{"Status": "Done", REPO_FIELD: "a"}),
    ]
    got = eligible(
        cards, status_names=STATUSES, repo_root=tmp_path, running_repos=set()
    )
    assert [c.number for c in got] == [1, 2]


def test_eligible_excludes_a_repo_already_running(tmp_path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    cards = [
        _card(1, **{"Status": "Todo", REPO_FIELD: "a"}),
        _card(2, **{"Status": "Todo", REPO_FIELD: "b"}),
    ]
    got = eligible(
        cards, status_names=STATUSES, repo_root=tmp_path, running_repos={"a"}
    )
    assert [c.number for c in got] == [2]


def test_eligible_excludes_a_card_with_an_unresolvable_repo(tmp_path) -> None:
    cards = [_card(1, **{"Status": "Todo", REPO_FIELD: "never-cloned"})]
    assert (
        eligible(cards, status_names=STATUSES, repo_root=tmp_path, running_repos=set())
        == []
    )


def test_eligible_excludes_a_closed_issue(tmp_path) -> None:
    (tmp_path / "a").mkdir()
    card = Card(
        item_id="I1",
        number=1,
        repo="o/r",
        url="u",
        title="t",
        body="b",
        state="CLOSED",
        fields={"Status": "Todo", REPO_FIELD: "a"},
    )
    assert (
        eligible([card], status_names=STATUSES, repo_root=tmp_path, running_repos=set())
        == []
    )


def test_rank_orders_by_priority_then_deadline_then_number() -> None:
    cards = [
        _card(5, Priority="Normal"),
        _card(3, Priority="Urgent", Deadline="2026-10-05"),
        _card(4, Priority="Urgent", Deadline="2026-10-01"),
        _card(1, Priority="Urgent"),
    ]
    assert [
        c.number
        for c in rank(
            cards,
            priority_order=("Urgent", "Important", "Normal"),
            field_names=FIELDS,
        )
    ] == [4, 3, 1, 5]


def test_rank_puts_an_unknown_priority_last() -> None:
    cards = [_card(1, Priority="Mystery"), _card(2, Priority="Urgent")]
    assert [
        c.number for c in rank(cards, priority_order=("Urgent",), field_names=FIELDS)
    ] == [2, 1]


def _report(**kw):
    base = dict(status="needs-review", summary="s", gate_cmd="make check", gate_exit=0)
    base.update(kw)
    return Report(**base)


def test_review_triggers_on_judgement() -> None:
    r = _report(judgement=("guessed at the behaviour",))
    assert "judgement" in review_triggers(
        r,
        _card(1),
        review_changed_files=5,
        vague_body_chars=400,
        acceptance_markers=("done when",),
    )


def test_review_triggers_on_diff_size() -> None:
    r = _report(changed=6)
    assert "changed" in review_triggers(
        r,
        _card(1),
        review_changed_files=5,
        vague_body_chars=400,
        acceptance_markers=("done when",),
    )


def test_review_triggers_on_a_card_with_no_acceptance_criteria() -> None:
    card = Card(
        item_id="I",
        number=1,
        repo="o/r",
        url="u",
        title="t",
        body="make it faster",
        state="OPEN",
        fields={},
    )
    assert "vague" in review_triggers(
        _report(),
        card,
        review_changed_files=5,
        vague_body_chars=400,
        acceptance_markers=("done when",),
    )


def test_a_card_with_a_checklist_is_not_vague() -> None:
    card = Card(
        item_id="I",
        number=1,
        repo="o/r",
        url="u",
        title="t",
        body="- [ ] add the flag\n- [ ] document it",
        state="OPEN",
        fields={},
    )
    assert (
        review_triggers(
            _report(),
            card,
            review_changed_files=5,
            vague_body_chars=400,
            acceptance_markers=("done when",),
        )
        == []
    )


def test_a_card_with_an_acceptance_marker_is_not_vague() -> None:
    card = Card(
        item_id="I",
        number=1,
        repo="o/r",
        url="u",
        title="t",
        body="make it faster. Done when the bench is under 2s.",
        state="OPEN",
        fields={},
    )
    assert (
        review_triggers(
            _report(),
            card,
            review_changed_files=5,
            vague_body_chars=400,
            acceptance_markers=("done when",),
        )
        == []
    )


def test_a_long_body_is_not_vague_even_without_markers() -> None:
    card = Card(
        item_id="I",
        number=1,
        repo="o/r",
        url="u",
        title="t",
        body="x" * 500,
        state="OPEN",
        fields={},
    )
    assert (
        review_triggers(
            _report(),
            card,
            review_changed_files=5,
            vague_body_chars=400,
            acceptance_markers=("done when",),
        )
        == []
    )
