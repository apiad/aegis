"""Writing fields and the pinned comment back onto a card."""

from __future__ import annotations

import json

import pytest

from aegis.workflows.builtins.afk.board import (
    COMMENT_LIMIT,
    BoardError,
    Card,
    Schema,
    parse_marker,
    render_marker,
    set_field,
    truncate_comment,
    upsert_comment,
)

SCHEMA = Schema(
    project_id="PVT_p",
    field_ids={"Status": "F_status", "Progress": "F_prog"},
    option_ids={"Status": {"Todo": "o_todo", "Running": "o_run"}},
)
CARD = Card(
    item_id="PVTI_1",
    number=12,
    repo="o/r",
    url="https://github.com/o/r/issues/12",
    title="t",
    body="b",
    state="OPEN",
    fields={},
)


def test_render_and_parse_marker_round_trip() -> None:
    body = "text above\n" + render_marker(
        task="t-9", tick="2026-09-25T02:10:00Z", attempt=1
    )
    assert parse_marker(body) == {
        "task": "t-9",
        "tick": "2026-09-25T02:10:00Z",
        "attempt": "1",
    }


def test_parse_marker_absent_is_empty() -> None:
    assert parse_marker("no marker here") == {}


def test_parse_marker_ignores_a_marker_inside_a_code_fence() -> None:
    """A card documenting this feature will quote the marker. The coordinator
    must not read its own example back as state."""
    body = "```\n<!-- aegis-afk task=EXAMPLE -->\n```\n" + render_marker(task="real")
    assert parse_marker(body) == {"task": "real"}


@pytest.mark.asyncio
async def test_set_field_resolves_a_single_select_option_id() -> None:
    calls: list[list[str]] = []

    async def run(argv):
        calls.append(argv)
        return "{}"

    await set_field(run, SCHEMA, CARD, field="Status", value="Running")
    assert "--single-select-option-id" in calls[0]
    assert "o_run" in calls[0]
    assert "--text" not in calls[0]


@pytest.mark.asyncio
async def test_set_field_writes_text_for_a_text_field() -> None:
    calls: list[list[str]] = []

    async def run(argv):
        calls.append(argv)
        return "{}"

    await set_field(run, SCHEMA, CARD, field="Progress", value="3/7")
    assert "--text" in calls[0] and "3/7" in calls[0]


@pytest.mark.asyncio
async def test_set_field_rejects_an_unknown_option() -> None:
    """An option that is not on the board cannot be written. For `Repo` this
    is the whitelist; for `Status` it catches a board whose columns were
    renamed out from under the config."""

    async def run(argv):
        return "{}"

    with pytest.raises(BoardError, match="Frobnicate"):
        await set_field(run, SCHEMA, CARD, field="Status", value="Frobnicate")


@pytest.mark.asyncio
async def test_set_field_rejects_an_unknown_field() -> None:
    async def run(argv):
        return "{}"

    with pytest.raises(BoardError, match="Waiting on"):
        await set_field(run, SCHEMA, CARD, field="Waiting on", value="#12")


@pytest.mark.asyncio
async def test_set_field_clears_with_none() -> None:
    calls: list[list[str]] = []

    async def run(argv):
        calls.append(argv)
        return "{}"

    await set_field(run, SCHEMA, CARD, field="Progress", value=None)
    assert "--clear" in calls[0]


def test_truncate_comment_leaves_a_short_body_alone() -> None:
    assert truncate_comment("hello") == "hello"


def test_truncate_comment_cuts_an_over_limit_body_and_says_so() -> None:
    """GitHub rejects a body over 65536 characters. Without this the card
    silently stops updating and nothing surfaces the reason."""
    out = truncate_comment("x" * (COMMENT_LIMIT + 500))
    assert len(out) <= COMMENT_LIMIT
    assert "truncated" in out


@pytest.mark.asyncio
async def test_upsert_comment_creates_when_no_marker_comment_exists() -> None:
    calls: list[list[str]] = []

    async def run(argv):
        calls.append(argv)
        if argv[1] == "api" and "comments" in argv[2]:
            return json.dumps([{"id": 1, "body": "a human said something"}])
        return "{}"

    assert await upsert_comment(run, CARD, body="hi") == "created"
    assert any("issues/12/comments" in " ".join(c) for c in calls)


@pytest.mark.asyncio
async def test_upsert_comment_edits_the_marked_comment_not_the_last_one() -> None:
    """`gh issue comment --edit-last` edits the authenticated user's last
    comment. The coordinator runs as that user, so after you reply to a card
    --edit-last would overwrite YOUR comment. Match on the marker instead."""
    calls: list[list[str]] = []

    async def run(argv):
        calls.append(argv)
        if "-X" not in argv and "comments" in " ".join(argv):
            return json.dumps(
                [
                    {"id": 7, "body": "### Coordinator\n" + render_marker(task="t-1")},
                    {"id": 8, "body": "looks good to me"},
                ]
            )
        return "{}"

    assert await upsert_comment(run, CARD, body="updated") == "edited"
    patch = [c for c in calls if "-X" in c][0]
    assert "issues/comments/7" in " ".join(patch)


@pytest.mark.asyncio
async def test_upsert_comment_surfaces_a_gh_failure_as_boarderror() -> None:
    """A rate-limited or unauthenticated gh must raise, so the caller leaves
    the card's status alone rather than moving it on a write that failed."""

    async def run(argv):
        raise RuntimeError("gh: API rate limit exceeded")

    with pytest.raises(BoardError, match="rate limit"):
        await upsert_comment(run, CARD, body="hi")
