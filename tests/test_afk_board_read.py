"""Parsing a GitHub Projects v2 payload into cards."""

from __future__ import annotations

import json

import pytest

from aegis.workflows.builtins.afk.board import (
    Card,
    parse_items,
    parse_schema,
)

ITEMS_PAYLOAD = {
    "data": {
        "organization": {
            "projectV2": {
                "id": "PVT_abc",
                "items": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [
                        {
                            "id": "PVTI_1",
                            "content": {
                                "__typename": "Issue",
                                "number": 12,
                                "title": "Add the parser",
                                "body": "Write a parser for X.",
                                "url": "https://github.com/o/r/issues/12",
                                "state": "OPEN",
                                "repository": {"nameWithOwner": "o/r"},
                            },
                            "fieldValues": {
                                "nodes": [
                                    {"__typename": "ProjectV2ItemFieldUserValue"},
                                    {
                                        "__typename": "ProjectV2ItemFieldSingleSelectValue",
                                        "name": "Todo",
                                        "field": {"name": "Status"},
                                    },
                                    {
                                        "__typename": "ProjectV2ItemFieldSingleSelectValue",
                                        "name": "aegis",
                                        "field": {"name": "Repo"},
                                    },
                                    {
                                        "__typename": "ProjectV2ItemFieldTextValue",
                                        "text": "3/7 - writing tests",
                                        "field": {"name": "Progress"},
                                    },
                                    {
                                        "__typename": "ProjectV2ItemFieldDateValue",
                                        "date": "2026-10-01",
                                        "field": {"name": "Deadline"},
                                    },
                                ]
                            },
                        },
                        {
                            "id": "PVTI_2",
                            "content": {"__typename": "DraftIssue"},
                            "fieldValues": {"nodes": []},
                        },
                    ],
                },
            }
        }
    }
}


def test_parse_items_returns_project_id_and_cards() -> None:
    project_id, cards = parse_items(ITEMS_PAYLOAD)
    assert project_id == "PVT_abc"
    assert len(cards) == 1
    c = cards[0]
    assert c.item_id == "PVTI_1"
    assert c.number == 12
    assert c.repo == "o/r"
    assert c.title == "Add the parser"
    assert c.body == "Write a parser for X."
    assert c.state == "OPEN"


def test_parse_items_keeps_field_names_verbatim() -> None:
    """Field names with spaces and mixed case survive. This is the whole
    reason the read is GraphQL rather than `gh project item-list`."""
    _, cards = parse_items(ITEMS_PAYLOAD)
    assert cards[0].fields == {
        "Status": "Todo",
        "Repo": "aegis",
        "Progress": "3/7 - writing tests",
        "Deadline": "2026-10-01",
    }


def test_parse_items_drops_draft_issues() -> None:
    """Draft issues have no comment thread, so the coordinator cannot report
    on them. They are not cards."""
    _, cards = parse_items(ITEMS_PAYLOAD)
    assert [c.number for c in cards] == [12]


def test_parse_items_drops_archived_items() -> None:
    """`items()` returns archived items alongside live ones. A card archived
    to get it off the board would otherwise be read back as work to do and
    handed to a worker."""
    payload = json.loads(json.dumps(ITEMS_PAYLOAD))
    nodes = payload["data"]["organization"]["projectV2"]["items"]["nodes"]
    nodes[0]["isArchived"] = True
    _, cards = parse_items(payload)
    assert cards == []


def test_parse_items_keeps_unarchived_items() -> None:
    payload = json.loads(json.dumps(ITEMS_PAYLOAD))
    payload["data"]["organization"]["projectV2"]["items"]["nodes"][0]["isArchived"] = (
        False
    )
    _, cards = parse_items(payload)
    assert [c.number for c in cards] == [12]


def test_items_query_asks_for_is_archived() -> None:
    """The filter is only as good as the field being selected. A query that
    never asks for isArchived makes every item read as unarchived."""
    from aegis.workflows.builtins.afk.board import ITEMS_QUERY

    assert "isArchived" in ITEMS_QUERY


def test_parse_items_handles_an_empty_board() -> None:
    payload = {
        "data": {
            "organization": {
                "projectV2": {
                    "id": "PVT_x",
                    "items": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [],
                    },
                }
            }
        }
    }
    assert parse_items(payload) == ("PVT_x", [])


def test_parse_schema_maps_fields_and_options() -> None:
    fields_payload = {
        "fields": [
            {
                "id": "F_status",
                "name": "Status",
                "type": "ProjectV2SingleSelectField",
                "options": [
                    {"id": "o_todo", "name": "Todo"},
                    {"id": "o_run", "name": "Running"},
                ],
            },
            {"id": "F_prog", "name": "Progress", "type": "ProjectV2Field"},
        ]
    }
    schema = parse_schema("PVT_abc", fields_payload)
    assert schema.project_id == "PVT_abc"
    assert schema.field_ids == {"Status": "F_status", "Progress": "F_prog"}
    assert schema.option_ids == {"Status": {"Todo": "o_todo", "Running": "o_run"}}


def test_parse_schema_option_lookup_is_the_repo_whitelist() -> None:
    """A Repo single-select's options are the whitelist. A value that is not
    an option cannot be set, so the schema is where the whitelist is read."""
    fields_payload = {
        "fields": [
            {
                "id": "F_repo",
                "name": "Repo",
                "type": "ProjectV2SingleSelectField",
                "options": [{"id": "o_a", "name": "aegis"}],
            },
        ]
    }
    schema = parse_schema("P", fields_payload)
    assert set(schema.option_ids["Repo"]) == {"aegis"}


@pytest.mark.asyncio
async def test_fetch_board_paginates() -> None:
    """A board past 50 items must not lose its tail — that is where the
    oldest un-run cards live."""
    from aegis.workflows.builtins.afk.board import fetch_board

    def page(cursor, has_next, number):
        return {
            "data": {
                "organization": {
                    "projectV2": {
                        "id": "PVT_p",
                        "items": {
                            "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
                            "nodes": [
                                {
                                    "id": f"PVTI_{number}",
                                    "content": {
                                        "__typename": "Issue",
                                        "number": number,
                                        "title": "t",
                                        "body": "",
                                        "url": "u",
                                        "state": "OPEN",
                                        "repository": {"nameWithOwner": "o/r"},
                                    },
                                    "fieldValues": {"nodes": []},
                                }
                            ],
                        },
                    }
                }
            }
        }

    calls: list[list[str]] = []
    responses = [
        json.dumps(page("CUR1", True, 1)),
        json.dumps(page(None, False, 2)),
        json.dumps({"fields": []}),
    ]

    async def run(argv):
        calls.append(argv)
        return responses.pop(0)

    schema, cards = await fetch_board(run, owner="o", owner_type="org", project=2)
    assert [c.number for c in cards] == [1, 2]
    assert "cursor=CUR1" in calls[1]
    assert schema.project_id == "PVT_p"


@pytest.mark.asyncio
async def test_fetch_board_uses_user_root_for_a_user_owner() -> None:
    """An org query against a user-owned project returns null and loses every
    card. The root is chosen from owner_type, not guessed."""
    from aegis.workflows.builtins.afk.board import fetch_board

    seen: list[str] = []

    async def run(argv):
        seen.append(" ".join(argv))
        if "graphql" in argv:
            return json.dumps(
                {
                    "data": {
                        "user": {
                            "projectV2": {
                                "id": "PVT_u",
                                "items": {
                                    "pageInfo": {"hasNextPage": False},
                                    "nodes": [],
                                },
                            }
                        }
                    }
                }
            )
        return json.dumps({"fields": []})

    await fetch_board(run, owner="apiad", owner_type="user", project=1)
    assert "user(login:" in seen[0]


@pytest.mark.asyncio
async def test_fetch_board_caches_the_schema_across_calls(monkeypatch) -> None:
    """`gh project field-list` is half this package's idle API cost, against a
    5000/hour limit shared with every other tool on the machine. A board's
    field ids change when a human edits the board, which is rare."""
    from aegis.workflows.builtins.afk import board as b

    b.clear_schema_cache()
    clock = {"t": 1000.0}
    monkeypatch.setattr(b, "now", lambda: clock["t"])

    calls: list[str] = []

    async def run(argv):
        calls.append("field-list" if "field-list" in argv else "graphql")
        if "field-list" in argv:
            return json.dumps(
                {
                    "fields": [
                        {
                            "id": "F",
                            "name": "Status",
                            "type": "ProjectV2SingleSelectField",
                            "options": [{"id": "o", "name": "Todo"}],
                        }
                    ]
                }
            )
        return json.dumps(ITEMS_PAYLOAD)

    for _ in range(3):
        schema, cards = await b.fetch_board(run, owner="o", owner_type="org", project=2)
        assert schema.field_ids == {"Status": "F"}
        assert [c.number for c in cards] == [12]

    assert calls.count("graphql") == 3, "cards must be re-read every time"
    assert calls.count("field-list") == 1, f"schema re-read: {calls}"


@pytest.mark.asyncio
async def test_the_schema_cache_expires(monkeypatch) -> None:
    """Stale for at most SCHEMA_TTL_S, so a field added by hand is picked up
    without restarting anything."""
    from aegis.workflows.builtins.afk import board as b

    b.clear_schema_cache()
    clock = {"t": 1000.0}
    monkeypatch.setattr(b, "now", lambda: clock["t"])

    calls: list[str] = []

    async def run(argv):
        if "field-list" in argv:
            calls.append("field-list")
            return json.dumps({"fields": []})
        return json.dumps(ITEMS_PAYLOAD)

    await b.fetch_board(run, owner="o", owner_type="org", project=2)
    clock["t"] += b.SCHEMA_TTL_S - 1
    await b.fetch_board(run, owner="o", owner_type="org", project=2)
    assert len(calls) == 1, "still inside the TTL"
    clock["t"] += 2
    await b.fetch_board(run, owner="o", owner_type="org", project=2)
    assert len(calls) == 2, "past the TTL it must re-read"


@pytest.mark.asyncio
async def test_the_schema_cache_is_keyed_by_board(monkeypatch) -> None:
    """Two boards must not share one schema — the field ids differ per
    project, so a cross-hit would write to the wrong field id."""
    from aegis.workflows.builtins.afk import board as b

    b.clear_schema_cache()
    monkeypatch.setattr(b, "now", lambda: 1000.0)
    seen: list[str] = []

    async def run(argv):
        if "field-list" in argv:
            seen.append(argv[3])  # the project number
            return json.dumps({"fields": []})
        return json.dumps(ITEMS_PAYLOAD)

    await b.fetch_board(run, owner="o", owner_type="org", project=2)
    await b.fetch_board(run, owner="o", owner_type="org", project=7)
    assert seen == ["2", "7"]
