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

import functools
import io
import json
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
TYPES = frozenset({"object", "array", "string", "integer", "number", "boolean"})
AGENT_FIELDS = frozenset({"prompt", "agent", "cwd", "result", "session"})
SCHEMA_KEYS = frozenset(
    {"type", "properties", "required", "items", "enum", "description"}
)
ID = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
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


# A {{...}} holds one expression; WHOLE is a string that is exactly one.
TEMPLATE = re.compile(r"\{\{(.*?)\}\}", re.S)
WHOLE = re.compile(r"^\{\{((?:(?!\}\}).)*)\}\}$", re.S)


def lookup(scope: dict, name: str, parts: list[str]):
    """``scope[name]`` down ``parts``. An agent's early outputs sit apart
    under ``scope[""]`` (no name is empty) and win for ``{{a.o}}``, whatever
    its result holds. A digit part indexes a list."""
    early = scope.get("", {}).get(name, {})
    if parts and parts[0] in early:
        v, parts = early[parts[0]], parts[1:]
    else:
        v = scope[name]
    for part in parts:
        v = v[int(part)] if isinstance(v, list) and part.isdigit() else v[part]
    return v


# -- expressions ---------------------------------------------------------------
# One evaluator for every {{...}} and every expr probe (spec §04):
#   expr := and ("or" and)*
#   and  := not ("and" not)*
#   not  := "not" not | cmp
#   cmp  := atom (("==" | "!=" | "<" | ">" | "<=" | ">=") atom)?
#   atom := ref | number | string | true | false | null | "(" expr ")"
#   ref  := name ("." key)*
# A tokenizer and a recursive descent build a tree of Ref, Lit and Op. Nothing
# reaches eval, an attribute or a call, so a definition an agent wrote can
# compare values and never run code.


class ExprError(ValueError):
    pass


@dataclass(frozen=True)
class Ref:
    name: str
    parts: tuple[str, ...] = ()


@dataclass(frozen=True)
class Lit:
    value: object


@dataclass(frozen=True)
class Op:
    op: str
    args: tuple


CMP = ("==", "!=", "<=", ">=", "<", ">")
WORDS = {"and", "or", "not", "true", "false", "null"}
_TOKEN = re.compile(
    r"""\s*(?:
      (?P<num>-?\d+(?:\.\d+)?(?![\w.]))
    | (?P<str>'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")
    | (?P<op>==|!=|<=|>=|<|>|\(|\))
    | (?P<ref>[A-Za-z_][\w-]*(?:\.[A-Za-z0-9_-]+)*)
    )""",
    re.X,
)
RENDER_ERRORS = (KeyError, TypeError, IndexError, ExprError)


def _tokens(text: str) -> list[tuple[str, str]]:
    out, i = [], 0
    while text[i:].strip():
        m = _TOKEN.match(text, i)
        if m is None:
            raise ExprError(f"cannot read {text[i:].strip()[:24]!r}")
        kind = m.lastgroup or ""
        val = m.group(kind)
        out.append(("word" if kind == "ref" and val in WORDS else kind, val))
        i = m.end()
    return out


class _Expr:
    def __init__(self, toks: list[tuple[str, str]]) -> None:
        self.t, self.i = toks, 0

    def peek(self) -> tuple[str, str]:
        return self.t[self.i] if self.i < len(self.t) else ("end", "")

    def take(self) -> tuple[str, str]:
        tok = self.peek()
        self.i += 1
        return tok

    def expr(self):
        node = self.conj()
        while self.peek() == ("word", "or"):
            self.take()
            node = Op("or", (node, self.conj()))
        return node

    def conj(self):
        node = self.neg()
        while self.peek() == ("word", "and"):
            self.take()
            node = Op("and", (node, self.neg()))
        return node

    def neg(self):
        if self.peek() == ("word", "not"):
            self.take()
            return Op("not", (self.neg(),))
        return self.cmp()

    def cmp(self):
        left = self.atom()
        kind, val = self.peek()
        if kind == "op" and val in CMP:
            self.take()
            node = Op(val, (left, self.atom()))
            if self.peek()[0] == "op" and self.peek()[1] in CMP:
                raise ExprError("comparisons do not chain: join them with and")
            return node
        return left

    def atom(self):
        kind, val = self.take()
        if kind == "num":
            return Lit(float(val) if "." in val else int(val))
        if kind == "str":
            return Lit(re.sub(r"\\(.)", r"\1", val[1:-1]))
        if kind == "word" and val in ("true", "false", "null"):
            return Lit({"true": True, "false": False, "null": None}[val])
        if kind == "ref":
            name, *parts = val.split(".")
            return Ref(name, tuple(parts))
        if (kind, val) == ("op", "("):
            node = self.expr()
            if self.take() != ("op", ")"):
                raise ExprError("a ( with no )")
            return node
        raise ExprError(f"expected a value, found {val or 'the end'}")


@functools.cache
def compile_expr(text: str):
    """The tree of one expression; ExprError says what is wrong with it."""
    p = _Expr(_tokens(text))
    if not p.t:
        raise ExprError("an empty expression")
    tree = p.expr()
    if p.i < len(p.t):
        raise ExprError(f"unexpected {p.peek()[1]!r}")
    return tree


def refs(tree) -> list[Ref]:
    if isinstance(tree, Ref):
        return [tree]
    if isinstance(tree, Op):
        out: list[Ref] = []
        for a in tree.args:
            out += [r for r in refs(a) if r not in out]
        return out
    return []


def _same(a, b) -> bool:
    num = (int, float)
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, num) and isinstance(b, num):
        return a == b
    return type(a) is type(b) and a == b


def _kind(v) -> str:
    return "null" if v is None else type(v).__name__


def evaluate(tree, scope: dict):
    """A tree's value. A name or field with no value is None (null), so
    ``or`` gives a default."""
    if isinstance(tree, Lit):
        return tree.value
    if isinstance(tree, Ref):
        try:
            return lookup(scope, tree.name, list(tree.parts))
        except (KeyError, IndexError, TypeError, ValueError):
            return None
    op, args = tree.op, tree.args
    if op == "or":
        a = evaluate(args[0], scope)
        return a if a else evaluate(args[1], scope)
    if op == "and":
        a = evaluate(args[0], scope)
        return evaluate(args[1], scope) if a else a
    if op == "not":
        return not evaluate(args[0], scope)
    a, b = evaluate(args[0], scope), evaluate(args[1], scope)
    if op == "==":
        return _same(a, b)
    if op == "!=":
        return not _same(a, b)
    nums = all(isinstance(x, int | float) and not isinstance(x, bool) for x in (a, b))
    if not nums and not (isinstance(a, str) and isinstance(b, str)):
        raise ExprError(
            f"{op} compares two numbers or two strings, not {_kind(a)} and {_kind(b)}"
        )
    return {"<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op]


def render(template: str, scope: dict, quote=None) -> str:
    """Each ``{{expr}}`` replaced by its value: a string as it is, anything
    else as JSON, then through ``quote`` (shlex.quote for a command). One
    that comes to null raises KeyError."""

    def one(m: re.Match) -> str:
        v = evaluate(compile_expr(m.group(1)), scope)
        if v is None:
            raise KeyError(
                f"{{{{{m.group(1).strip()}}}}} has no value; give a default with or"
            )
        text = v if isinstance(v, str) else json.dumps(v)
        return quote(text) if quote else text

    return TEMPLATE.sub(one, template)


def value_of(raw, scope: dict):
    """A string that is one ``{{expr}}`` gives the expression's value, not
    its text (a map's over, a loop's max, a wait); anything else is itself."""
    if isinstance(raw, str) and (m := WHOLE.match(raw)):
        return evaluate(compile_expr(m.group(1)), scope)
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
                if sub and t.kind == "map" and not t.children:
                    continue  # its do was refused, with its own error
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
                if (held := self._held(n, t, ancestors)) is not None:
                    p, branch = held
                    self.err(
                        path,
                        f"{target} is in another branch of the par at {p.path}, "
                        f"whose cap {p.body['cap']} is below its {len(p.children)} "
                        "branches: this branch holds its slot while it waits, so "
                        f"that one may never start. Put the after on {branch.id}, "
                        "or raise the cap",
                    )
                    continue
                edge(point, ("S", n.id))
                if sub and t.kind == "agent":
                    wanted = waited.setdefault(t.id, [])
                    if sub not in wanted:
                        wanted.append(sub)
        for m in nodes.values():
            if m.kind == "map" and m.body.get("as") in nodes:
                self.err(
                    f"{m.path}.map.as",
                    f"{m.body['as']} is a node id; name the item otherwise",
                )
        for n in nodes.values():
            afters = {a for x in [n, *ancestors(n)] for a in x.after}
            items = {nodes[mid].body.get("as") for mid in n.maps}  # enclosing maps
            for path, tree in self._exprs(n):
                for r in refs(tree):
                    name, first = r.name, (r.parts or ("",))[0]
                    if name == "args":
                        continue
                    if name in items:
                        continue  # an enclosing map's item; the innermost wins
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
        for aid, wanted in waited.items():
            # {{a.o}} reads the output, so a result property of that name
            # would never be read.
            result = nodes[aid].body.get("result")
            props = result.get("properties") if isinstance(result, dict) else None
            for o in wanted:
                if isinstance(props, dict) and o in props:
                    self.err(
                        f"{nodes[aid].path}.agent.result.properties.{o}",
                        f"{o} is an early output an after waits on; "
                        "name the result's property otherwise",
                    )
        return waited

    @staticmethod
    def _held(n: Node, t: Node, ancestors) -> tuple[Node, Node] | None:
        """The capped par and the branch, if any, where ``n`` waits inside
        that branch on ``t`` in another. A begun branch holds its slot until
        it finishes (workflows._many), so with fewer slots than branches the
        one it waits on may never begin. A branch that waits itself has begun
        nothing. A map's items cannot wait on each other: the map contains
        them."""
        up = [n, *ancestors(n)]
        t_up = {x.id for x in [t, *ancestors(t)]}
        for i, p in enumerate(up[1:], 1):
            branch = up[i - 1]
            if (
                p.kind == "par"
                and p.body.get("cap", len(p.children)) < len(p.children)
                and branch is not n
                and branch.id not in t_up
                and p.id in t_up
            ):
                return p, branch
        return None

    def _exprs(self, n: Node) -> list[tuple[str, object]]:
        """Every expression a node holds, with the YAML path of its string:
        each {{...}} in a template string, and an expr probe whole. A string
        that does not compile is an error here and yields nothing."""
        out = []
        for path, text in self._strings(n):
            rest = TEMPLATE.sub("", text)
            if "{{" in rest:
                self.err(path, "a {{ with no }}")
            for m in TEMPLATE.finditer(text):
                try:
                    out.append((path, compile_expr(m.group(1))))
                except ExprError as e:
                    self.err(path, f"{{{{{m.group(1).strip()}}}}}: {e}")
        for path, text in self._bare(n):
            try:
                out.append((path, compile_expr(text)))
            except ExprError as e:
                self.err(path, f"{text}: {e}")
        return out

    @staticmethod
    def _bare(n: Node) -> list[tuple[str, str]]:
        return []  # task 4 adds expr probes and judge inputs

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
    # The server's max_agents shape: total for every agent together, and a
    # number per agent alias for that alias alone.
    limits = raw.get("limits") or {}
    if not isinstance(limits, dict) or any(
        isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in limits.values()
    ):
        p.err("$.limits", "limits is {total: N, <agent>: M}, each a positive integer")
        limits = {}
    for k, v in limits.items():
        if k == "total":
            continue
        if k not in agents:
            p.err(
                f"$.limits.{k}",
                f"{k} is not an agent in .aegis.yaml; limits takes total and agent names",
            )
        elif "total" in limits and v > limits["total"]:
            p.err(f"$.limits.{k}", f"{k} {v} is above total {limits['total']}")
    root = p.node(raw.get("root"), "$.root") if "root" in raw else None
    if root is None and not any(e.path.startswith("$.root") for e in p.errors):
        p.err("$.root", "a workflow needs a root node")
    waited: dict[str, list[str]] = {}
    if root is not None:
        waited = p.order(root)
    for path, v in p.caps:
        if "total" in limits and v > limits["total"]:
            p.err(path, f"cap {v} is above limits.total {limits['total']}")
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
