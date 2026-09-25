"""Reading and writing a GitHub Projects v2 board, over the `gh` CLI.

Reads go through GraphQL rather than `gh project item-list --format json`,
because that command lowercases custom field names into JSON keys
("Categoria" -> "categoria"), which silently mangles any field whose name
carries a space. Field names are part of this package's contract with the
operator's board, so they are read verbatim.
"""

from __future__ import annotations

import json
import re
import shlex
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


COMMENT_LIMIT = 65536
MARKER_RE = re.compile(r"<!--\s*aegis-afk\s+(?P<kv>[^>]*?)\s*-->")
_FENCE_RE = re.compile(r"```.*?```", re.S)


class BoardError(RuntimeError):
    """A board read or write did not happen. The caller must not move a card
    on the strength of a write that failed."""


def render_marker(**kv: object) -> str:
    """The coordinator's bookkeeping, as an HTML comment.

    Values are shell-quoted, so one containing a space survives the round
    trip. Without that, ``gate=make check`` parses back as ``gate=make``:
    the coordinator would re-run a different command from the one it gave
    the worker and measure the wrong thing, which is the one comparison
    this whole design exists to make.
    """
    parts = []
    for k, v in kv.items():
        text = str(v)
        if "-->" in text:
            # Would terminate the comment early and strand every later key.
            raise BoardError(f"marker value for {k!r} contains '-->'")
        parts.append(f"{k}={shlex.quote(text)}")
    return f"<!-- aegis-afk {' '.join(parts)} -->"


def parse_marker(body: str) -> dict[str, str]:
    """The coordinator's own bookkeeping, or {}.

    Fenced blocks are stripped first: a card documenting this feature quotes
    the marker, and reading an example back as state would point the reaper
    at a task id that never existed.
    """
    m = MARKER_RE.search(_FENCE_RE.sub("", body or ""))
    if not m:
        return {}
    out: dict[str, str] = {}
    try:
        tokens = shlex.split(m.group("kv"))
    except ValueError:
        # An unbalanced quote someone hand-edited in. No state is better
        # than half of it: a partial marker would point the reaper at a
        # task id with no gate beside it.
        return {}
    for tok in tokens:
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k] = v
    return out


def truncate_comment(body: str, *, limit: int = COMMENT_LIMIT) -> str:
    """GitHub rejects a comment body over `limit`. A silent rejection stops
    the card updating with nothing to read, so cut and say that we cut."""
    if len(body) <= limit:
        return body
    note = "\n\n_(truncated: the full report exceeded GitHub's comment limit)_"
    return body[: limit - len(note)] + note


def gh_command(argv: list[str]) -> str:
    """An argv list as one shell command line.

    Every token is `shlex.quote`d, which is not decoration. The previous
    version quoted with `json.dumps`, i.e. double quotes, and bash expands
    inside those: the GraphQL query lost its newlines to literal `\\n` AND
    lost `$owner`, `$num` and `$cursor` to parameter expansion, so the API
    answered `UNKNOWN_CHAR ("n") at [1, 1]`. Single quotes pass both through.
    """
    return " ".join(shlex.quote(a) for a in argv)


async def run_gh(engine, argv: list[str]) -> str:
    """Run a `gh` command through the engine's bash and return stdout.

    Raises BoardError on a non-zero exit, so a caller never moves a card on
    the strength of a read or write that did not happen.
    """
    res = await engine.bash(gh_command(argv))
    if res.get("exit") != 0:
        raise BoardError(
            f"{argv[:3]} exited {res.get('exit')}: {str(res.get('stdout'))[:300]}"
        )
    return res.get("stdout") or ""


async def set_field(
    run: Runner, schema: Schema, card: Card, *, field: str, value: str | None
) -> None:
    field_id = schema.field_ids.get(field)
    if field_id is None:
        raise BoardError(f"board has no field named {field!r}")
    argv = [
        "gh",
        "project",
        "item-edit",
        "--id",
        card.item_id,
        "--project-id",
        schema.project_id,
        "--field-id",
        field_id,
    ]
    if value is None:
        argv.append("--clear")
    elif field in schema.option_ids:
        option_id = schema.option_ids[field].get(value)
        if option_id is None:
            raise BoardError(
                f"{field!r} has no option named {value!r} "
                f"(options: {sorted(schema.option_ids[field])})"
            )
        argv += ["--single-select-option-id", option_id]
    else:
        argv += ["--text", value]
    try:
        await run(argv)
    except Exception as e:  # noqa: BLE001 - transport failures are all alike here
        raise BoardError(f"setting {field!r} on #{card.number}: {e}") from e


async def upsert_comment(run: Runner, card: Card, *, body: str) -> str:
    """Rewrite the coordinator's pinned comment in place, or create it.

    Found by marker rather than `gh issue comment --edit-last`: that flag
    targets the authenticated user's most recent comment, and the coordinator
    authenticates as the operator, so once the operator replies to a card
    --edit-last would overwrite their reply.
    """
    body = truncate_comment(body)
    try:
        raw = await run(
            [
                "gh",
                "api",
                f"repos/{card.repo}/issues/{card.number}/comments",
                "--paginate",
            ]
        )
        existing = json.loads(raw)
        mine = next((c for c in existing if parse_marker(c.get("body") or "")), None)
        if mine is None:
            await run(
                [
                    "gh",
                    "api",
                    f"repos/{card.repo}/issues/{card.number}/comments",
                    "-f",
                    f"body={body}",
                ]
            )
            return "created"
        await run(
            [
                "gh",
                "api",
                "-X",
                "PATCH",
                f"repos/{card.repo}/issues/comments/{mine['id']}",
                "-f",
                f"body={body}",
            ]
        )
        return "edited"
    except BoardError:
        raise
    except Exception as e:  # noqa: BLE001
        raise BoardError(f"commenting on #{card.number}: {e}") from e
