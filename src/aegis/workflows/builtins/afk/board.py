"""Reading and writing a GitHub Projects v2 board, over the `gh` CLI.

Reads go through GraphQL rather than `gh project item-list --format json`,
because that command lowercases custom field names into JSON keys
("Categoria" -> "categoria"), which silently mangles any field whose name
carries a space. Field names are part of this package's contract with the
operator's board, so they are read verbatim.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field as dc_field
from typing import Awaitable, Callable

Runner = Callable[[list[str]], Awaitable[str]]

ITEMS_QUERY = """
query($owner:String!,$num:Int!,$cursor:String){
  OWNER_ROOT(login:$owner){
    projectV2(number:$num){
      id
      items(first:50, after:$cursor){
        pageInfo{hasNextPage endCursor}
        nodes{
          id
          isArchived
          content{
            __typename
            ... on Issue { number title body url state repository{nameWithOwner} }
          }
          fieldValues(first:20){
            nodes{
              __typename
              ... on ProjectV2ItemFieldTextValue {
                text field{... on ProjectV2FieldCommon{name}} }
              ... on ProjectV2ItemFieldDateValue {
                date field{... on ProjectV2FieldCommon{name}} }
              ... on ProjectV2ItemFieldSingleSelectValue {
                name field{... on ProjectV2FieldCommon{name}} }
            }
          }
        }
      }
    }
  }
}
"""


@dataclass(frozen=True)
class Card:
    """One issue on the board, with its project field values."""

    item_id: str
    number: int
    repo: str
    url: str
    title: str
    body: str
    state: str
    fields: dict[str, str] = dc_field(default_factory=dict)


@dataclass(frozen=True)
class Schema:
    """Field and option ids, which every write needs by id rather than name."""

    project_id: str
    field_ids: dict[str, str]
    option_ids: dict[str, dict[str, str]]


def _root(payload: dict) -> dict:
    data = payload.get("data") or {}
    for key in ("organization", "user"):
        node = data.get(key)
        if node:
            return node["projectV2"]
    raise ValueError("no projectV2 in payload")


def _field_values(raw_nodes: list[dict]) -> dict[str, str]:
    """Flatten the field-value union into {field name: string value}.

    Nodes with no `field` are the built-in user/label/repository values,
    which carry no name and are not part of this package's contract.
    """
    out: dict[str, str] = {}
    for node in raw_nodes or ():
        name = (node.get("field") or {}).get("name")
        if not name:
            continue
        for key in ("text", "date", "name"):
            if node.get(key) is not None:
                out[name] = str(node[key])
                break
    return out


def parse_items(payload: dict) -> tuple[str, list[Card]]:
    """(project_id, cards). Draft issues are dropped: they have no comment
    thread, and the report is the most valuable thing produced per card.

    Archived items are dropped too. `items()` returns them alongside live
    ones, so a card archived to get it off the board would otherwise be read
    back as work to do and dispatched to a worker.
    """
    project = _root(payload)
    cards: list[Card] = []
    for node in project["items"]["nodes"]:
        if node.get("isArchived"):
            continue
        content = node.get("content") or {}
        if content.get("__typename") != "Issue":
            continue
        cards.append(
            Card(
                item_id=node["id"],
                number=content["number"],
                repo=content["repository"]["nameWithOwner"],
                url=content["url"],
                title=content["title"],
                body=content.get("body") or "",
                state=content["state"],
                fields=_field_values((node.get("fieldValues") or {}).get("nodes", [])),
            )
        )
    return project["id"], cards


def parse_schema(project_id: str, fields_payload: dict) -> Schema:
    field_ids: dict[str, str] = {}
    option_ids: dict[str, dict[str, str]] = {}
    for f in fields_payload.get("fields") or ():
        field_ids[f["name"]] = f["id"]
        if f.get("options"):
            option_ids[f["name"]] = {o["name"]: o["id"] for o in f["options"]}
    return Schema(project_id=project_id, field_ids=field_ids, option_ids=option_ids)


async def fetch_board(
    run: Runner, *, owner: str, owner_type: str, project: int
) -> tuple[Schema, list[Card]]:
    """Every card on the board, plus the ids needed to write to it.

    Paginates: a board past 50 items would otherwise silently lose its tail,
    and the tail is where the oldest un-run cards sit.
    """
    root = "organization" if owner_type == "org" else "user"
    query = ITEMS_QUERY.replace("OWNER_ROOT", root)
    cards: list[Card] = []
    project_id = ""
    cursor: str | None = None
    while True:
        argv = [
            "gh",
            "api",
            "graphql",
            "-F",
            f"owner={owner}",
            "-F",
            f"num={project}",
            "-f",
            f"query={query}",
        ]
        if cursor:
            argv += ["-F", f"cursor={cursor}"]
        payload = json.loads(await run(argv))
        project_id, page = parse_items(payload)
        cards.extend(page)
        info = _root(payload)["items"]["pageInfo"]
        if not info.get("hasNextPage"):
            break
        cursor = info["endCursor"]

    fields_raw = json.loads(
        await run(
            [
                "gh",
                "project",
                "field-list",
                str(project),
                "--owner",
                owner,
                "--format",
                "json",
                "--limit",
                "50",
            ]
        )
    )
    return parse_schema(project_id, fields_raw), cards
