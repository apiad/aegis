"""The workflow language (docs/superpowers/specs/2026-10-15-dynamic-workflows-design.html).

A workflow is one YAML document: a header and a tree of nodes. A node is a
mapping whose kind key holds the kind's body, with the common fields (id,
say, after, deadline, retries, see) beside it. Every node says, in one
sentence for the person, what it does: the workflow is the plan they approve.

``parse`` reads, lints and assigns an id to every node without one, in one
pass, and returns every error with its YAML path, so an agent fixes the file
in one round. This version runs ``seq`` and ``agent``; the lint knows every
kind the spec names and refuses the others by name, so a later slice adds a
kind here as one branch.
"""

from __future__ import annotations

import io
import re
from collections.abc import Iterator
from dataclasses import dataclass, field

from ruamel.yaml import YAML, YAMLError

KINDS = (
    "seq",
    "par",
    "map",
    "loop",
    "if",
    "agent",
    "sh",
    "check",
    "wait",
    "gate",
    "ref",
)
RUNS = frozenset({"seq", "agent"})  # the kinds this version runs
COMMON = frozenset({"id", "say", "after", "deadline", "retries", "see"})
AGENT_FIELDS = frozenset({"prompt", "agent", "cwd", "result", "class", "session"})
SCHEMA_KEYS = frozenset(
    {"type", "properties", "required", "items", "enum", "description"}
)
REF = re.compile(r"\{\{\s*([A-Za-z_][\w]*)((?:\.[A-Za-z_][\w]*)*)\s*\}\}")
ID = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")


@dataclass(frozen=True)
class LintError:
    path: str
    message: str


@dataclass
class Node:
    id: str
    kind: str
    say: str
    path: str
    body: dict = field(default_factory=dict)
    children: list["Node"] = field(default_factory=list)


@dataclass
class Workflow:
    id: str
    title: str
    say: str
    args: dict
    root: Node
    text: str


def render(template: str, scope: dict) -> str:
    """``{{a.b}}`` replaced by ``scope["a"]["b"]``; a missing name raises KeyError."""

    def one(m: re.Match) -> str:
        v = scope[m.group(1)]
        for part in filter(None, m.group(2).split(".")):
            v = v[part]
        return str(v)

    return REF.sub(one, template)


def check_value(value, schema: dict, path: str = "$") -> list[str]:
    """The subset of JSON Schema a result may declare: type, properties,
    required, items, enum. Enough to hold a worker to its report's shape
    without a dependency."""
    out: list[str] = []
    t = schema.get("type")
    ok = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, int | float) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        None: True,
    }.get(t, True)
    if not ok:
        return [f"{path}: expected {t}"]
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{path}: expected one of {schema['enum']}")
    if t == "object":
        for k in schema.get("required", []):
            if k not in value:
                out.append(f"{path}: missing {k}")
        for k, sub in (schema.get("properties") or {}).items():
            if k in value:
                out += check_value(value[k], sub, f"{path}.{k}")
    if t == "array" and "items" in schema:
        for i, v in enumerate(value):
            out += check_value(v, schema["items"], f"{path}[{i}]")
    return out


def walk(node: Node) -> Iterator[Node]:
    yield node
    for c in node.children:
        yield from walk(c)


def rows(wf: Workflow) -> list[dict]:
    """The tree as the card draws it: depth-first, one row per node."""
    out: list[dict] = []

    def go(n: Node, depth: int) -> None:
        row: dict = {"id": n.id, "kind": n.kind, "say": n.say, "depth": depth}
        if n.kind == "agent":
            row |= {"agent": n.body.get("agent"), "prompt": n.body.get("prompt", "")}
        out.append(row)
        for c in n.children:
            go(c, depth + 1)

    go(wf.root, 0)
    return out


class _Parser:
    def __init__(self, agents: set[str]) -> None:
        self.agents = agents
        self.errors: list[LintError] = []
        self.seen: dict[str, str] = {}  # id -> path
        self.n = 0

    def err(self, path: str, message: str) -> None:
        self.errors.append(LintError(path, message))

    def node(self, raw, path: str) -> Node | None:
        if not isinstance(raw, dict):
            self.err(path, "a node is a mapping with one kind key")
            return None
        kinds = [k for k in raw if k in KINDS]
        unknown = [k for k in raw if k not in KINDS and k not in COMMON]
        for k in unknown:
            self.err(f"{path}.{k}", f"{k} is not a node field")
        if len(kinds) != 1:
            self.err(
                path, f"a node has exactly one kind key, one of {', '.join(KINDS)}"
            )
            return None
        kind = kinds[0]
        if kind not in RUNS:
            self.err(path, f"{kind} is not in this version yet")
            return None
        say = raw.get("say")
        if not isinstance(say, str) or not say.strip():
            self.err(path, "every node needs say: one sentence for the person")
            say = ""
        nid = raw.get("id")
        if nid is None:
            # Only nodes without an id are numbered, so n1, n2… read in order.
            self.n += 1
            nid = f"n{self.n}"
            while nid in self.seen:
                self.n += 1
                nid = f"n{self.n}"
        elif not isinstance(nid, str) or not ID.match(nid):
            self.err(f"{path}.id", "an id is lowercase letters, digits, - and _")
        elif nid in self.seen:
            self.err(path, f"id {nid} is already used at {self.seen[nid]}")
        self.seen.setdefault(str(nid), path)
        node = Node(str(nid), kind, say, path)
        body = raw[kind]
        if kind == "seq":
            if not isinstance(body, list) or not body:
                self.err(f"{path}.seq", "seq holds a non-empty list of nodes")
            else:
                for i, child in enumerate(body):
                    c = self.node(child, f"{path}.seq[{i}]")
                    if c is not None:
                        node.children.append(c)
        elif kind == "agent":
            node.body = self.agent(body, f"{path}.agent")
        return node

    def agent(self, body, path: str) -> dict:
        if not isinstance(body, dict):
            self.err(path, "agent holds a mapping with prompt and agent")
            return {}
        for k in body:
            if k not in AGENT_FIELDS:
                self.err(f"{path}.{k}", f"{k} is not an agent field")
        for k in ("class", "session"):
            if k in body:
                self.err(f"{path}.{k}", f"{k} is not in this version yet")
        if not isinstance(body.get("prompt"), str) or not body["prompt"].strip():
            self.err(f"{path}.prompt", "an agent node needs a prompt")
        if body.get("agent") not in self.agents:
            self.err(path, f"no agent {body.get('agent')} in .aegis.yaml")
        if "result" in body:
            self.schema(body["result"], f"{path}.result")
        return dict(body)

    def schema(self, s, path: str) -> None:
        if not isinstance(s, dict):
            self.err(path, "a result schema is a mapping")
            return
        for k, v in s.items():
            if k not in SCHEMA_KEYS:
                self.err(
                    f"{path}.{k}",
                    f"{k} is not in the schema subset ({', '.join(sorted(SCHEMA_KEYS))})",
                )
            elif k == "properties" and isinstance(v, dict):
                for name, sub in v.items():
                    self.schema(sub, f"{path}.properties.{name}")
            elif k == "items":
                self.schema(v, f"{path}.items")

    def references(self, root: Node) -> None:
        """Each {{name}} in a say or prompt names args or a node that has
        finished before this one starts: an earlier node in the same seq, or
        anything inside one."""
        done: set[str] = set()

        def strings(n: Node) -> list[tuple[str, str]]:
            out = [(n.path + ".say", n.say)]
            if n.kind == "agent" and isinstance(n.body.get("prompt"), str):
                out.append((n.path + ".agent.prompt", n.body["prompt"]))
            return out

        def visit(n: Node) -> None:
            for path, text in strings(n):
                for m in REF.finditer(text):
                    if m.group(1) != "args" and m.group(1) not in done:
                        self.err(path, f"{m.group(1)} is not an earlier node or args")
            for c in n.children:
                visit(c)
                done.update(x.id for x in walk(c))

        visit(root)


def parse(text: str, agents: set[str]) -> tuple[Workflow | None, list[LintError]]:
    try:
        raw = YAML(typ="safe").load(io.StringIO(text))
    except YAMLError as e:
        return None, [LintError("$", f"the YAML does not parse: {e}")]
    p = _Parser(agents)
    if not isinstance(raw, dict):
        return None, [
            LintError("$", "a workflow is a mapping with id, title, say and root")
        ]
    for k in raw:
        if k not in (
            "id",
            "title",
            "say",
            "args",
            "limits",
            "deadline",
            "blocks",
            "root",
        ):
            p.err(f"$.{k}", f"{k} is not a workflow field")
    for k in ("limits", "deadline", "blocks"):
        if k in raw:
            p.err(f"$.{k}", f"{k} is not in this version yet")
    wid = raw.get("id")
    if not isinstance(wid, str) or not ID.match(wid):
        p.err("$.id", "a workflow needs an id: lowercase letters, digits, - and _")
    for k in ("title", "say"):
        if not isinstance(raw.get(k), str) or not raw[k].strip():
            p.err(f"$.{k}", f"a workflow needs {k}")
    args = raw.get("args") or {}
    if not isinstance(args, dict):
        p.err("$.args", "args is a JSON Schema mapping")
        args = {}
    root = p.node(raw.get("root"), "$.root") if "root" in raw else None
    if root is None and not any(e.path.startswith("$.root") for e in p.errors):
        p.err("$.root", "a workflow needs a root node")
    if root is not None:
        p.references(root)
    if p.errors or root is None:
        return None, p.errors
    return Workflow(wid, raw["title"], raw["say"], args, root, text), []
