"""The workflow language (docs/superpowers/specs/2026-10-15-dynamic-workflows-design.html).

A workflow is one YAML document: a header and a tree of nodes. A node is a
mapping whose kind key holds the kind's body, with the common fields (id,
say, after, deadline, retries, see) beside it. Every node says, in one
sentence for the person, what it does: the workflow is the plan they approve.

``parse`` reads, lints and assigns an id to every node without one, in one
pass, and returns every error with its YAML path, so an agent fixes the file
in one round. This version runs ``seq``, ``par``, ``map`` and ``agent``,
with ``after``, caps and limits; the lint knows every kind the spec names and
refuses the others by name, so a later slice adds a kind here as one branch.
An ``after`` and every ``{{reference}}`` are checked on one graph of start and
finish points (``_Parser.order``).
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
RUNS = frozenset({"seq", "par", "map", "agent"})  # the kinds this version runs
COMMON = frozenset({"id", "say", "after", "deadline", "retries", "see", "cap"})
LATER = ("deadline", "retries")  # common fields no engine honours yet
MAP_FIELDS = frozenset({"over", "as", "cap", "do"})
CLASSES = frozenset({"heavy"})  # the classes a limit names
TYPES = frozenset({"object", "array", "string", "integer", "number", "boolean"})
AGENT_FIELDS = frozenset({"prompt", "agent", "cwd", "result", "class", "session"})
SCHEMA_KEYS = frozenset(
    {"type", "properties", "required", "items", "enum", "description"}
)
REF = re.compile(r"\{\{\s*([A-Za-z_][\w]*)((?:\.[A-Za-z_][\w]*)*)\s*\}\}")
ID = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
WHOLE = re.compile(r"^\{\{\s*([A-Za-z_][\w]*)((?:\.[A-Za-z_][\w]*)*)\s*\}\}$")
KEY = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
AFTER = re.compile(r"^[a-z][a-z0-9_-]{0,39}(\.[A-Za-z0-9_-]{1,40})?$")


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
    after: list[str] = field(default_factory=list)
    maps: tuple[str, ...] = ()  # the maps whose do holds this node, outermost first


@dataclass
class Workflow:
    id: str
    title: str
    say: str
    args: dict
    root: Node
    text: str
    limits: dict = field(default_factory=dict)
    # Agent id -> the early outputs some after waits on, for its preamble.
    waited: dict[str, list[str]] = field(default_factory=dict)


def lookup(scope: dict, name: str, parts: list[str]):
    v = scope[name]
    for part in parts:
        v = v[part]
    return v


def render(template: str, scope: dict) -> str:
    """``{{a.b}}`` replaced by ``scope["a"]["b"]``; a missing name raises KeyError."""
    return REF.sub(
        lambda m: str(
            lookup(scope, m.group(1), list(filter(None, m.group(2).split("."))))
        ),
        template,
    )


def value_of(raw, scope: dict):
    """A map's ``over``: a literal list as it is, or the value of a string
    that is one reference, not rendered to text."""
    if isinstance(raw, str) and (m := WHOLE.match(raw)):
        return lookup(scope, m.group(1), list(filter(None, m.group(2).split("."))))
    return raw


def item_keys(items: list) -> list[str] | str:
    """Each item's key in instance paths and in ``after`` (``images.base``):
    its id when it is a mapping with one, else its position. A string says
    why the items have no keys."""
    keys: list[str] = []
    for i, it in enumerate(items):
        k = str(it["id"]) if isinstance(it, dict) and "id" in it else str(i)
        if not KEY.match(k):
            return f"item {i}'s id {k!r} is not letters, digits, - and _"
        if k in keys:
            return f"two items have the key {k}"
        keys.append(k)
    return keys


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


def rows(wf: Workflow, items=lambda path: None) -> list[dict]:
    """The tree as the card draws it: depth-first, one row per node
    instance. ``items(path)`` is a started map's ``[[key, value], ...]``;
    each item's nodes then appear once per item under ``<map>.<key>.``. A
    map not started shows its do once, each row marked ``each``."""
    out: list[dict] = []

    def go(n: Node, depth: int, prefix: str, each: bool) -> None:
        path = prefix + n.id
        row: dict = {
            "id": path,
            "node": n.id,
            "kind": n.kind,
            "say": n.say,
            "depth": depth,
        }
        if each:
            row["each"] = True
        if n.kind == "agent":
            row |= {
                "agent": n.body.get("agent"),
                "prompt": n.body.get("prompt", ""),
                "class": n.body.get("class"),
            }
        if n.kind in ("map", "par") and n.body.get("cap"):
            row["cap"] = n.body["cap"]
        if n.kind == "map":
            row["as"] = n.body.get("as")
        out.append(row)
        if n.kind == "map":
            got = None if each else items(path)
            if got is None:
                go(n.children[0], depth + 1, prefix, True)
            else:
                for key, _ in got:
                    go(n.children[0], depth + 1, f"{path}.{key}.", False)
        else:
            for c in n.children:
                go(c, depth + 1, prefix, each)

    go(wf.root, 0, "", False)
    return out


class _Parser:
    def __init__(self, agents: set[str]) -> None:
        self.agents = agents
        self.errors: list[LintError] = []
        self.seen: dict[str, str] = {}  # id -> path
        self.n = 0
        self.maps: list[str] = []  # the maps whose do is being parsed
        self.caps: list[tuple[str, int]] = []  # each cap, for limits.agents

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
        for k in LATER:
            if k in raw:
                self.err(f"{path}.{k}", f"{k} is not in this version yet")
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
        elif nid == "args":
            self.err(f"{path}.id", "args is reserved for the workflow's arguments")
        elif nid in self.seen:
            self.err(path, f"id {nid} is already used at {self.seen[nid]}")
        self.seen.setdefault(str(nid), path)
        node = Node(str(nid), kind, say, path, maps=tuple(self.maps))
        body = raw[kind]
        after = raw.get("after", [])
        if not isinstance(after, list) or not all(
            isinstance(a, str) and AFTER.match(a) for a in after
        ):
            self.err(
                f"{path}.after", "after is a list of node ids, or id.item and id.output"
            )
            after = []
        node.after = list(after)
        if "cap" in raw:
            if kind != "par":
                self.err(
                    f"{path}.cap", "cap sits beside par; a map's cap goes inside map"
                )
            elif self.cap(raw["cap"], f"{path}.cap"):
                node.body = {"cap": raw["cap"]}
        if kind in ("seq", "par"):
            if not isinstance(body, list) or not body:
                self.err(f"{path}.{kind}", f"{kind} holds a non-empty list of nodes")
            else:
                for i, child in enumerate(body):
                    c = self.node(child, f"{path}.{kind}[{i}]")
                    if c is not None:
                        node.children.append(c)
        elif kind == "map":
            node.body = self.map(body, f"{path}.map", node)
        elif kind == "agent":
            node.body = self.agent(body, f"{path}.agent")
        return node

    def cap(self, v, path: str) -> bool:
        if isinstance(v, bool) or not isinstance(v, int) or v < 1:
            self.err(path, "cap is a positive integer")
            return False
        self.caps.append((path, v))
        return True

    def map(self, body, path: str, node: Node) -> dict:
        if not isinstance(body, dict):
            self.err(path, "map holds a mapping with over, as and do")
            return {}
        for k in body:
            if k not in MAP_FIELDS:
                self.err(f"{path}.{k}", f"{k} is not a map field")
        over = body.get("over")
        if not isinstance(over, list) and not (
            isinstance(over, str) and WHOLE.match(over)
        ):
            self.err(f"{path}.over", "over is a list, or one {{reference}} to a list")
        name = body.get("as")
        if not isinstance(name, str) or not ID.match(name) or name == "args":
            self.err(
                f"{path}.as", "as names the item: lowercase letters, digits, - and _"
            )
        if "cap" in body:
            self.cap(body["cap"], f"{path}.cap")
        if "do" not in body:
            self.err(f"{path}.do", "map needs do: the node each item runs")
        else:
            self.maps.append(node.id)
            c = self.node(body["do"], f"{path}.do")
            self.maps.pop()
            if c is not None:
                node.children.append(c)
        return {k: body[k] for k in ("over", "as", "cap") if k in body}

    def agent(self, body, path: str) -> dict:
        if not isinstance(body, dict):
            self.err(path, "agent holds a mapping with prompt and agent")
            return {}
        for k in body:
            if k not in AGENT_FIELDS:
                self.err(f"{path}.{k}", f"{k} is not an agent field")
        if "session" in body:
            self.err(f"{path}.session", "session is not in this version yet")
        if "class" in body and body["class"] not in CLASSES:
            self.err(f"{path}.class", "class is heavy or absent")
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
            elif k == "type" and v not in TYPES:
                self.err(
                    f"{path}.type",
                    f"{v} is not a type, one of {', '.join(sorted(TYPES))}",
                )
            elif k in ("required", "enum") and not isinstance(v, list):
                self.err(f"{path}.{k}", f"{k} is a list")
            elif k == "properties" and isinstance(v, dict):
                for name, sub in v.items():
                    self.schema(sub, f"{path}.properties.{name}")
            elif k == "items":
                self.schema(v, f"{path}.items")

    def order(self, root: Node) -> dict[str, list[str]]:
        """What can happen before what. Each node has a start S and a finish
        F; an edge says the first point comes before the second. A parent
        starts before its children and finishes after them; a seq child
        finishes before the next starts; an after target finishes (an item:
        the map's do finishes; an output: the agent starts) before the node
        starts. Tree edges never cycle, so an after whose node already
        reaches its target closes one, and the run could never finish. A
        reference is sound when its node's finish reaches this node's start,
        or when after names it. Returns the outputs some after waits on."""
        nodes = {n.id: n for n in walk(root)}
        parent = {c.id: n for n in nodes.values() for c in n.children}
        edges: dict[tuple[str, str], set[tuple[str, str]]] = {}

        def edge(a: tuple[str, str], b: tuple[str, str]) -> None:
            edges.setdefault(a, set()).add(b)

        def reach(a: tuple[str, str], b: tuple[str, str]) -> bool:
            seen, todo = {a}, [a]
            while todo:
                x = todo.pop()
                if x == b:
                    return True
                for y in edges.get(x, ()):
                    if y not in seen:
                        seen.add(y)
                        todo.append(y)
            return False

        def ancestors(n: Node) -> list[Node]:
            out = []
            while n.id in parent:
                n = parent[n.id]
                out.append(n)
            return out

        def visible(t: Node, n: Node) -> bool:
            return n.maps[: len(t.maps)] == t.maps

        for n in nodes.values():
            edge(("S", n.id), ("F", n.id))
            for c in n.children:
                edge(("S", n.id), ("S", c.id))
                edge(("F", c.id), ("F", n.id))
            if n.kind == "seq":
                for a, b in zip(n.children, n.children[1:]):
                    edge(("F", a.id), ("S", b.id))
        waited: dict[str, list[str]] = {}
        for n in nodes.values():
            for i, a in enumerate(n.after):
                path = f"{n.path}.after[{i}]"
                target, _, sub = a.partition(".")
                t = nodes.get(target)
                if t is None:
                    self.err(path, f"{target} is not a node")
                    continue
                if t.id == n.id or t.id in {x.id for x in ancestors(n)}:
                    self.err(
                        path, f"{target} contains this node, so it cannot finish first"
                    )
                    continue
                if not visible(t, n):
                    m = t.maps[-1]
                    self.err(
                        path,
                        f"{target} is inside map {m}; wait on {m} or one of its items",
                    )
                    continue
                if sub and t.kind not in ("map", "agent"):
                    self.err(
                        path,
                        f"{target} is a {t.kind}: only a map has items "
                        "and only an agent has outputs",
                    )
                    continue
                if sub and t.kind == "agent":
                    point = ("S", t.id)
                elif sub:
                    point = ("F", t.children[0].id)
                else:
                    point = ("F", t.id)
                if reach(("S", n.id), point):
                    self.err(
                        path,
                        f"waiting on {target} closes a cycle: "
                        f"{target} cannot finish before this node starts",
                    )
                    continue
                edge(point, ("S", n.id))
                if sub and t.kind == "agent":
                    wanted = waited.setdefault(t.id, [])
                    if sub not in wanted:
                        wanted.append(sub)
        names = {n.body.get("as"): n for n in nodes.values() if n.kind == "map"}
        for name, m in names.items():
            if name in nodes:
                self.err(
                    f"{m.path}.map.as", f"{name} is a node id; name the item otherwise"
                )
        for n in nodes.values():
            afters = {a for x in [n, *ancestors(n)] for a in x.after}
            for path, text in self._strings(n):
                for m in REF.finditer(text):
                    name, first = m.group(1), (m.group(2).split(".") + ["", ""])[1]
                    if name == "args":
                        continue
                    if name in names and names[name].id in n.maps:
                        continue  # an enclosing map's item
                    t = nodes.get(name)
                    if t is None:
                        self.err(path, f"{name} is not an earlier node or args")
                    elif not visible(t, n):
                        m = t.maps[-1]
                        self.err(
                            path,
                            f"{name} is inside map {m}; wait on {m} or one of its items",
                        )
                    elif not (
                        reach(("F", t.id), ("S", n.id)) or f"{name}.{first}" in afters
                    ):
                        self.err(
                            path,
                            f"{name} may not have finished before this node starts: "
                            "put it earlier in a seq or name it in after",
                        )
        return waited

    @staticmethod
    def _strings(n: Node) -> list[tuple[str, str]]:
        out = [(n.path + ".say", n.say)]
        for k in ("prompt", "cwd"):
            if n.kind == "agent" and isinstance(n.body.get(k), str):
                out.append((f"{n.path}.agent.{k}", n.body[k]))
        if n.kind == "map" and isinstance(n.body.get("over"), str):
            out.append((n.path + ".map.over", n.body["over"]))
        return out


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
    for k in ("deadline", "blocks"):
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
    limits = raw.get("limits") or {}
    if not isinstance(limits, dict) or any(
        k not in ("agents", "heavy")
        or isinstance(v, bool)
        or not isinstance(v, int)
        or v < 1
        for k, v in limits.items()
    ):
        p.err("$.limits", "limits is {agents: N, heavy: M}, each a positive integer")
        limits = {}
    elif "agents" in limits and limits.get("heavy", 0) > limits["agents"]:
        p.err("$.limits.heavy", "heavy is at most agents")
    root = p.node(raw.get("root"), "$.root") if "root" in raw else None
    if root is None and not any(e.path.startswith("$.root") for e in p.errors):
        p.err("$.root", "a workflow needs a root node")
    waited: dict[str, list[str]] = {}
    if root is not None:
        waited = p.order(root)
    for path, v in p.caps:
        if "agents" in limits and v > limits["agents"]:
            p.err(path, f"cap {v} is above limits.agents {limits['agents']}")
    if p.errors or root is None:
        return None, p.errors
    return (
        Workflow(
            wid,
            raw["title"],
            raw["say"],
            args,
            root,
            text,
            limits=limits,
            waited=waited,
        ),
        [],
    )
