"""The workflow language (docs/superpowers/specs/2026-10-15-dynamic-workflows-design.html).

A workflow is one YAML document: a header and a tree of nodes. A node is a
mapping whose kind key holds the kind's body, with the common fields (id,
say, after, deadline, retries, see) beside it. Every node says, in one
sentence for the person, what it does: the workflow is the plan they approve.

``parse`` reads, lints and assigns an id to every node without one, in one
pass, and returns every error with its YAML path, so an agent fixes the file
in one round. This version reads every kind the spec names, with ``after``,
caps, limits, deadlines, retries, blocks and ``defs``. An ``after`` and every ``{{expression}}`` are
checked on one graph of start and finish points (``_Parser.order``). An id is
unique in its scope (the top level, a map's item, a loop's round) and a node's
``key`` names it in the whole file; a ``ref`` is expanded here, at parse.
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
RUNS = frozenset(KINDS)  # the kinds this version runs
COMMON = frozenset({"id", "say", "after", "deadline", "retries", "see", "cap"})
MAP_FIELDS = frozenset({"over", "as", "cap", "do"})
TYPES = frozenset({"object", "array", "string", "integer", "number", "boolean"})
AGENT_FIELDS = frozenset({"prompt", "agent", "cwd", "result", "session"})
SCHEMA_KEYS = frozenset(
    {"type", "properties", "required", "items", "enum", "description"}
)
ID = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
KEY = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
AFTER = re.compile(r"^[a-z][a-z0-9_-]{0,39}(\.[A-Za-z0-9_-]{1,40})?$")

DURATION = re.compile(r"^(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$")
RETRYABLE = frozenset({"agent", "sh", "check"})
MAX_RETRIES = 10
MAX_ROUNDS = 1000
MAX_NESTING = 64  # nodes inside nodes, fixed whatever the caller's stack
PROBE_EVERY_S = 30.0
MIN_EVERY_S = 5.0
JUDGE_FIELDS = frozenset({"agent", "prompt", "inputs"})


def duration(text) -> float | None:
    """Seconds in ``1h30m``: days, hours, minutes, seconds, each optional,
    in that order. None for anything else, and for zero."""
    if not isinstance(text, str) or not (m := DURATION.match(text.strip())):
        return None
    d, h, mi, s = (int(x or 0) for x in m.groups())
    total = ((d * 24 + h) * 60 + mi) * 60 + s
    return float(total) if total > 0 else None


def fmt_duration(secs: float) -> str:
    """Seconds written back as a duration: ``2s``, ``20m``, ``1h30m``."""
    rest, out = int(secs), ""
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if rest >= size:
            out += f"{rest // size}{unit}"
            rest %= size
    return out or "0s"


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
    # The maps and loops whose do holds this node, outermost first.
    maps: tuple[str, ...] = ()
    deadline: float | None = None  # seconds from its start
    retries: int = 0
    use: str = ""  # the YAML path of the ref that put this node here
    branch: str = ""  # then or else, for the root of an if's branch
    generated: bool = False  # its id was numbered, not written
    see: str = ""  # a path into the plan, filled like say

    @property
    def key(self) -> str:
        """Unique in the workflow: an id is unique only in its scope (the top
        level, a map's item, a loop's round), so the enclosing ids come first."""
        return "/".join((*self.maps, self.id))


def template_key(inst: str) -> str:
    """An instance path's template key: ``images.base.impl`` is node impl
    in item base of map images, so every other segment is a node id."""
    return "/".join(inst.split(".")[0::2])


def find(by_key: dict[str, Node], name: str, maps: tuple[str, ...]) -> Node | None:
    """The node ``name`` means from inside ``maps``: the innermost scope first."""
    for depth in range(len(maps), -1, -1):
        if (n := by_key.get("/".join((*maps[:depth], name)))) is not None:
            return n
    return None


@dataclass
class Workflow:
    id: str
    title: str
    say: str
    args: dict
    root: Node
    text: str
    limits: dict = field(default_factory=dict)
    deadline: float | None = None  # seconds from approval
    # Agent key -> the early outputs some after waits on, for its preamble.
    waited: dict[str, list[str]] = field(default_factory=dict)
    by_key: dict[str, Node] = field(default_factory=dict)
    defs: dict = field(default_factory=dict)
    # A node's key -> the node that holds it, for the deadlines above it.
    parent: dict[str, Node] = field(default_factory=dict)


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


MAX_DEPTH = 64  # parentheses and nots inside one another


class _Expr:
    def __init__(self, toks: list[tuple[str, str]]) -> None:
        self.t, self.i, self.depth = toks, 0, 0

    def deeper(self) -> None:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise ExprError(f"nested more than {MAX_DEPTH} deep")

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
            self.deeper()
            node = Op("not", (self.neg(),))
            self.depth -= 1
            return node
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
            self.deeper()
            node = self.expr()
            if self.take() != ("op", ")"):
                raise ExprError("a ( with no )")
            self.depth -= 1
            return node
        raise ExprError(f"expected a value, found {val or 'the end'}")


@functools.cache
def compile_expr(text: str):
    """The tree of one expression; ExprError says what is wrong with it."""
    p = _Expr(_tokens(text))
    if not p.t:
        raise ExprError("an empty expression")
    try:
        tree = p.expr()
    except RecursionError:  # MAX_DEPTH stops it first; never crash a lint
        raise ExprError("nested too deep") from None
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


def _value(expr: str, scope: dict) -> str:
    """A template's value as text: a string as it is, anything else as
    JSON. One that comes to null raises KeyError."""
    v = evaluate(compile_expr(expr), scope)
    if v is None:
        raise KeyError(f"{{{{{expr.strip()}}}}} has no value; give a default with or")
    return v if isinstance(v, str) else json.dumps(v)


def render(template: str, scope: dict) -> str:
    """Each ``{{expr}}`` replaced by its value (see _value). Never for a
    command: shell_command passes values through the environment."""
    return TEMPLATE.sub(lambda m: _value(m.group(1), scope), template)


WORD, QUOTED = "word", "quoted"  # how a template in a command is written
INT_CMD = re.compile(
    r"(?:^|[;&|(){}\n`]|\$\(|\b(?:then|do|else)\b)\s*(?:(let)\s|(?:declare|typeset|local|readonly|export)"
    r"((?:\s+[-+][A-Za-z]+)+))"
)
NAME_ARG = re.compile(
    r"(?:(?:^|\s)-v\s+|^\s*(?:read|mapfile|readarray|unset|declare|typeset|local"
    r"|readonly|export)\b[^<>]*\s)$"
)
ARITH = ("((", "$((", "$[")


def shell_spots(cmd: str) -> dict[int, tuple[str, str]]:
    """Where each ``{{...}}`` in a command sits, by its offset: WORD (a bare
    word, or inside $(...) or backticks), QUOTED (double quotes or an
    unquoted heredoc), or the place that refuses it. shell_command writes
    a WORD as "$AEGIS_Vn" and a QUOTED as ${AEGIS_Vn}, so a value is never
    command text: a misread place gives a wrong literal at worst. Refused
    are the places that keep it literal (single quotes, $'...', a quoted
    heredoc) and those where bash runs a $(...) inside a variable's value
    (arithmetic, [[ ]], a subscript, a variable's name)."""
    spots: dict[int, tuple[str, str]] = {}
    m = INT_CMD.search(cmd)
    integer = bool(m and (m.group(1) or set("in") & set(m.group(2))))
    stack: list[str] = []  # ' $' " ` $( ( (( $(( $[ [[ [ ${ ${w
    heredocs: list[tuple[str, bool, bool]] = []  # delimiter, expands, strips tabs
    i, n = 0, len(cmd)

    def where(start: int) -> str:
        if integer:
            return "a let or declare -i command"
        if any(s in ARITH for s in stack):
            return "arithmetic"
        if "[[" in stack:
            return "a [[ ... ]] test"
        if "${" in stack:
            return "a ${...} expansion"
        if "[" in stack:
            return "an array subscript"
        quoting = [s for s in stack if s in ("'", "$'", '"', "`", "$(")]
        top = quoting[-1] if quoting else None
        if top == "'":
            return "single quotes"
        if top == "$'":
            return "a $'...' string"
        if top == '"':
            return QUOTED
        seg = re.split(r"[;&|\n(`]", cmd[:start])[-1]
        return "a variable's name" if NAME_ARG.search(seg) else WORD

    def word_start(j: int) -> bool:
        return j == 0 or cmd[j - 1] in " \t\n;&|()<>"

    while i < n:
        if cmd.startswith("{{", i) and (m := TEMPLATE.match(cmd, i)):
            spots[i] = (m.group(0), where(i))
            i = m.end()
            continue
        c = cmd[i]
        top = stack[-1] if stack else None
        if top == "'":
            if c == "'":
                stack.pop()
            i += 1
            continue
        if top == "$'":
            if c == "\\":
                i += 2
                continue
            if c == "'":
                stack.pop()
            i += 1
            continue
        if c == "\\":
            i += 2
            continue
        if top == '"':
            if c == '"':
                stack.pop()
            elif cmd.startswith("$((", i):
                stack.append("$((")
                i += 2
            elif cmd.startswith("$(", i):
                stack.append("$(")
                i += 1
            elif cmd.startswith("${", i):
                stack.append("${")
                i += 1
            elif cmd.startswith("$[", i):
                stack.append("$[")
                i += 1
            elif c == "`":
                stack.append("`")
            i += 1
            continue
        if top == "${":
            if op := re.match(r":?[-=+?]", cmd[i:]):
                stack[-1] = "${w"  # the word after the operator
                i += op.end()
                continue
            if c == "}":
                stack.pop()
            elif c == "[":
                stack.append("[")
            i += 1
            continue
        if top in ("((", "$((") and cmd.startswith("))", i):
            stack.pop()
            i += 2
            continue
        if top == "${w" and c == "}":
            stack.pop()
            i += 1
            continue
        if top == "$[" and c == "]":
            stack.pop()
            i += 1
            continue
        if top == "[" and c == "]":
            stack.pop()
            i += 1
            continue
        if top == "[[" and cmd.startswith("]]", i) and word_start(i):
            stack.pop()
            i += 2
            continue
        if top == "`" and c == "`":
            stack.pop()
            i += 1
            continue
        # A bare word, or inside a $(...), a subshell or an arithmetic.
        if c == "'" or c == '"':
            stack.append(c)
        elif cmd.startswith("$'", i):
            stack.append("$'")
            i += 1
        elif c == "`":
            stack.append("`")
        elif cmd.startswith("$((", i):
            stack.append("$((")
            i += 2
        elif cmd.startswith("$(", i):
            stack.append("$(")
            i += 1
        elif cmd.startswith("${", i):
            stack.append("${")
            i += 1
        elif cmd.startswith("$[", i):
            stack.append("$[")
            i += 1
        elif cmd.startswith("((", i):
            stack.append("((")
            i += 1
        elif c == "(":
            stack.append("(")
        elif c == ")" and stack:
            stack.pop()
        elif cmd.startswith("[[", i) and word_start(i):
            stack.append("[[")
            i += 1
        elif c == "[" and i and (cmd[i - 1].isalnum() or cmd[i - 1] == "_"):
            stack.append("[")
        elif c == "#" and word_start(i) and top in (None, "(", "$(", "`"):
            end = cmd.find("\n", i)
            end = n if end < 0 else end
            for t in TEMPLATE.finditer(cmd, i, end):
                spots[t.start()] = (t.group(0), WORD)  # a comment, never read
            i = end
            continue
        elif (
            cmd.startswith("<<", i)
            and not cmd.startswith("<<<", i)
            and not any(s in ARITH or s == "[[" for s in stack)
        ):
            hd = re.match(r"<<(-?)[ \t]*((?:[^\s;&|<>()]|\\.)+)", cmd[i:])
            if hd:
                word = hd.group(2)
                expands = not any(q in word for q in "'\"\\")
                heredocs.append(
                    (re.sub(r"['\"\\]", "", word), expands, bool(hd.group(1)))
                )
                i += hd.end()
                continue
        elif c == "\n" and heredocs:
            i += 1
            for delim, expands, tabs in heredocs:
                while i < n:
                    end = cmd.find("\n", i)
                    end = n if end < 0 else end
                    line = cmd[i:end]
                    for t in TEMPLATE.finditer(cmd, i, end):
                        w = "a let or declare -i command" if integer else QUOTED
                        spots[t.start()] = (
                            t.group(0),
                            w if expands else "a quoted heredoc",
                        )
                    i = end + 1
                    if (line.lstrip("\t") if tabs else line) == delim:
                        break
            heredocs = []
            continue
        i += 1
    return spots


def shell_command(template: str, scope: dict) -> tuple[str, dict[str, str]]:
    """A command to run and the environment it needs: the ``n``-th
    ``{{expr}}`` becomes "$AEGIS_Vn" as a word, ${AEGIS_Vn} inside double
    quotes or a heredoc, and its value (see _value) is AEGIS_Vn."""
    spots, env = shell_spots(template), {}

    def one(m: re.Match) -> str:
        name = f"AEGIS_V{len(env) + 1}"
        env[name] = _value(m.group(1), scope)
        quoted = spots.get(m.start(), ("", WORD))[1] == QUOTED
        return f"${{{name}}}" if quoted else f'"${name}"'

    return TEMPLATE.sub(one, template), env


def refused_templates(cmd: str) -> list[tuple[str, str]]:
    """Each ``{{...}}`` the lint refuses in a command, with where it sits."""
    spots = shell_spots(cmd)
    return [spots[at] for at in sorted(spots) if spots[at][1] not in (WORD, QUOTED)]


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
    instance. ``items(path)`` is a started map's ``[[key, value], ...]``,
    or a started loop's ``[[round, None], ...]``; each item's or round's
    nodes then appear once per item under ``<map>.<key>.``. A map or loop
    not started shows its do once, each row marked ``each``."""
    out: list[dict] = []

    def go(n: Node, depth: int, prefix: str, each: bool, under: str = "") -> None:
        path = prefix + n.id
        row: dict = {
            "id": path,
            "node": n.key,
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
            if n.body.get("session") == "main":
                row["main"] = True  # the main agent runs it, in its own session
        if n.kind in ("map", "par") and n.body.get("cap"):
            row["cap"] = n.body["cap"]
        if n.kind == "map":
            row["as"] = n.body.get("as")
        if n.kind == "sh":
            row["cmd"] = n.body.get("sh", "")
        if n.body.get("probe"):
            row["probe"] = n.body["probe"]
        if n.kind == "wait":
            row["wait"] = n.body.get("wait")
        if n.kind == "gate":
            row |= {k: n.body.get(k) for k in ("by", "ask", "show")}
        if n.kind == "loop":
            row["max"] = n.body.get("max")
        if n.branch:
            row |= {"branch": n.branch, "if": under}
        if n.deadline is not None:
            row["deadline"] = n.deadline
        if n.retries:
            row["retries"] = n.retries
        if n.see:
            row["see"] = n.see
        out.append(row)
        if n.kind in ("map", "loop"):
            got = None if each else items(path)
            if got is None:
                go(n.children[0], depth + 1, prefix, True)
            else:
                for key, _ in got:
                    go(n.children[0], depth + 1, f"{path}.{key}.", False)
        else:
            for c in n.children:
                go(c, depth + 1, prefix, each, path)

    go(wf.root, 0, "", False)
    return out


class _Parser:
    def __init__(self, agents: set[str]) -> None:
        self.agents = agents
        self.errors: list[LintError] = []
        # (scope, id) -> path: an id is unique in its scope, the enclosing
        # maps' and loops' ids.
        self.seen: dict[tuple[tuple[str, ...], str], str] = {}
        self.n = 0
        self.maps: list[str] = []  # the maps and loops whose do is being parsed
        self.blocks: dict = {}
        self.defs: dict = {}
        self.used: set[str] = set()  # the blocks some ref uses
        self.using: list[tuple[str, str]] = []  # (block, ref path) being expanded
        self.caps: list[tuple[str, int]] = []  # each cap, for limits.agents
        self.depth = 0  # the nodes being parsed, outermost first

    def err(self, path: str, message: str) -> None:
        self.errors.append(LintError(path, message))

    def node(self, raw, path: str) -> Node | None:
        if self.depth >= MAX_NESTING:
            self.err(path, f"nodes nest at most {MAX_NESTING} levels deep")
            return None
        self.depth += 1
        try:
            return self._node(raw, path)
        finally:
            self.depth -= 1

    def _node(self, raw, path: str) -> Node | None:
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
        if kind == "ref":
            return self.ref(raw, path)
        say = raw.get("say")
        if not isinstance(say, str) or not say.strip():
            self.err(path, "every node needs say: one sentence for the person")
            say = ""
        nid = raw.get("id")
        scope = tuple(self.maps)
        if nid is None:
            # Only nodes without an id are numbered, so n1, n2… read in order.
            self.n += 1
            nid = f"n{self.n}"
            while (scope, nid) in self.seen:
                self.n += 1
                nid = f"n{self.n}"
        elif not isinstance(nid, str) or not ID.match(nid):
            self.err(f"{path}.id", "an id is lowercase letters, digits, - and _")
        elif nid == "args":
            self.err(f"{path}.id", "args is reserved for the workflow's arguments")
        elif (scope, nid) in self.seen:
            where = (
                f" (block {self.using[-1][0]}, used at {self.using[-1][1]})"
                if self.using
                else ""
            )
            self.err(
                path, f"id {nid} is already used at {self.seen[(scope, nid)]}{where}"
            )
        self.seen.setdefault((scope, str(nid)), path)
        node = Node(
            str(nid),
            kind,
            say,
            path,
            maps=scope,
            use=self.using[-1][1] if self.using else "",
            generated="id" not in raw,
        )
        if "deadline" in raw:
            node.deadline = duration(raw["deadline"])
            if node.deadline is None:
                self.err(
                    f"{path}.deadline", "a deadline is a duration such as 20m or 1h30m"
                )
        if "see" in raw:
            see = raw["see"]
            if not isinstance(see, str) or not see.strip():
                self.err(
                    f"{path}.see",
                    "see is a path to a file, with an optional #fragment or :line",
                )
            elif re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", see):
                self.err(
                    f"{path}.see",
                    "see is a path to a file in the run's folder, not a URL",
                )
            else:
                node.see = see
        if "retries" in raw:
            r = raw["retries"]
            if (
                isinstance(r, bool)
                or not isinstance(r, int)
                or not 0 <= r <= MAX_RETRIES
            ):
                self.err(
                    f"{path}.retries",
                    f"retries is a whole number from 0 to {MAX_RETRIES}",
                )
            elif kind not in RETRYABLE:
                self.err(
                    path + ".retries",
                    "retries go on agent, sh and check; repeat a group with loop",
                )
            else:
                node.retries = r
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
        elif kind == "sh":
            if not isinstance(body, str) or not body.strip():
                self.err(f"{path}.sh", "sh holds the command, as one string")
            else:
                self.command(body, f"{path}.sh")
            node.body = {"sh": body}
        elif kind == "check":
            node.body = {"probe": self.probe(body, f"{path}.check")}
        elif kind == "wait":
            if duration(body) is None and not (
                isinstance(body, str) and WHOLE.match(body)
            ):
                self.err(
                    f"{path}.wait",
                    "wait is a duration such as 20m, or one {{expression}}",
                )
            node.body = {"wait": body}
        elif kind == "gate":
            node.body = self.gate(body, f"{path}.gate")
        elif kind == "if":
            self.branch_if(body, f"{path}.if", node)
        elif kind == "loop":
            self.loop(body, f"{path}.loop", node)
        return node

    def gate(self, body, path: str) -> dict:
        """A stop for the main agent or the person (spec §04): ask is the
        question, show the values the decider sees, rendered once."""
        if not isinstance(body, dict):
            self.err(path, "gate holds ask, by and an optional show")
            return {"by": None, "ask": "", "show": []}
        for k in body:
            if k not in ("ask", "by", "show"):
                self.err(f"{path}.{k}", f"{k} is not a gate field")
        if body.get("by") not in ("main", "person"):
            self.err(f"{path}.by", "a gate is answered by main or by person")
        if not isinstance(body.get("ask"), str) or not body["ask"].strip():
            self.err(f"{path}.ask", "a gate needs ask: the question")
        show = body.get("show", [])
        if not isinstance(show, list) or not all(isinstance(s, str) for s in show):
            self.err(
                f"{path}.show",
                "show is a list of references, such as impl or review.verdict",
            )
            show = []
        return {"by": body.get("by"), "ask": body.get("ask", ""), "show": show}

    def ref(self, raw: dict, path: str) -> Node | None:
        """A use of a block: a copy of its root, with the ref's common
        fields over the root's. Its ids live in the scope it is used in."""
        name = raw["ref"]
        if not isinstance(name, str) or name not in self.blocks:
            self.err(f"{path}.ref", f"no block {name} in blocks")
            return None
        if any(u == name for u, _ in self.using):
            self.err(f"{path}.ref", f"block {name} uses itself")
            return None
        block = self.blocks[name]
        if not isinstance(block, dict):
            return None  # refused at $.blocks.<name>
        merged = {k: v for k, v in block.items()}
        for k in ("id", "say", "after", "deadline", "retries", "see"):
            if k in raw:
                merged[k] = raw[k]
        self.used.add(name)
        self.using.append((name, path))
        try:
            return self.node(merged, f"$.blocks.{name}")
        finally:
            self.using.pop()

    def command(self, cmd: str, path: str) -> None:
        """A command's values arrive through its environment (shell_command):
        refused where that value would stay literal or be run as code."""
        for tpl, where in refused_templates(cmd):
            why = (
                "where it stays literal; write it bare or inside double quotes"
                if where in ("single quotes", "a $'...' string", "a quoted heredoc")
                else "where bash would run a $(...) in its value; never use a "
                "value there"
            )
            self.err(path, f"{tpl} sits inside {where}, {why}")

    def probe(self, raw, path: str) -> dict:
        """One of three probes (spec §04): sh passes on exit 0, expr on a
        truthy value, judge on an agent's yes."""
        kinds = [
            k for k in ("sh", "expr", "judge") if isinstance(raw, dict) and k in raw
        ]
        if len(kinds) != 1:
            self.err(path, "a probe is one of sh, expr and judge")
            return {}
        kind = kinds[0]
        for k in raw:
            if k not in (kind, "every") or (k == "every" and kind != "sh"):
                self.err(f"{path}.{k}", f"{k} is not a field of a {kind} probe")
        if kind in ("sh", "expr"):
            if not isinstance(raw[kind], str) or not raw[kind].strip():
                self.err(f"{path}.{kind}", f"{kind} is a non-empty string")
                return {}
            out: dict = {kind: raw[kind]}
            if kind == "sh":
                self.command(raw["sh"], f"{path}.sh")
                every = duration(raw["every"]) if "every" in raw else PROBE_EVERY_S
                if every is None or every < MIN_EVERY_S:
                    self.err(f"{path}.every", "every is a duration of at least 5s")
                    every = PROBE_EVERY_S
                out["every"] = every
            return out
        j = raw["judge"]
        if not isinstance(j, dict):
            self.err(f"{path}.judge", "judge holds agent, prompt and inputs")
            return {}
        for k in j:
            if k not in JUDGE_FIELDS:
                self.err(f"{path}.judge.{k}", f"{k} is not a judge field")
        if j.get("agent") not in self.agents:
            self.err(f"{path}.judge", f"no agent {j.get('agent')} in .aegis.yaml")
        if not isinstance(j.get("prompt"), str) or not j["prompt"].strip():
            self.err(f"{path}.judge.prompt", "a judge needs a prompt")
        inputs = j.get("inputs", [])
        if not isinstance(inputs, list) or not all(isinstance(i, str) for i in inputs):
            self.err(
                f"{path}.judge.inputs",
                "inputs is a list of references, such as impl or review.verdict",
            )
            inputs = []
        return {
            "judge": {
                "agent": j.get("agent"),
                "prompt": j.get("prompt", ""),
                "inputs": inputs,
            }
        }

    def branch_if(self, body, path: str, node: Node) -> None:
        if not isinstance(body, dict) or "when" not in body or "then" not in body:
            self.err(path, "if holds when, then and an optional else")
            return
        for k in body:
            if k not in ("when", "then", "else"):
                self.err(f"{path}.{k}", f"{k} is not an if field")
        node.body = {"probe": self.probe(body["when"], f"{path}.when")}
        for b in ("then", "else"):
            if b in body and (c := self.node(body[b], f"{path}.{b}")) is not None:
                c.branch = b
                node.children.append(c)

    def loop(self, body, path: str, node: Node) -> None:
        if not isinstance(body, dict) or "do" not in body:
            self.err(path, "loop holds do, max and an optional until")
            return
        for k in body:
            if k not in ("do", "max", "until"):
                self.err(f"{path}.{k}", f"{k} is not a loop field")
        mx = body.get("max")
        whole = isinstance(mx, str) and WHOLE.match(mx)
        if not whole and (
            isinstance(mx, bool) or not isinstance(mx, int) or not 1 <= mx <= MAX_ROUNDS
        ):
            self.err(
                f"{path}.max",
                f"a loop needs max: a whole number from 1 to {MAX_ROUNDS}, or one {{{{expression}}}}",
            )
        node.body = {"max": mx}
        # A round is a scope, as a map's item is: its nodes are l.<n>.<id>,
        # and until reads the round it follows.
        self.maps.append(node.id)
        if "until" in body:
            node.body["probe"] = self.probe(body["until"], f"{path}.until")
        c = self.node(body["do"], f"{path}.do")
        self.maps.pop()
        if c is not None:
            node.children.append(c)

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
        sess = body.get("session")
        if sess is not None and sess != "main":
            self.err(
                f"{path}.session",
                f"session: {sess} is not in this version yet; session: main runs "
                "the step in the main agent's session",
            )
        if not isinstance(body.get("prompt"), str) or not body["prompt"].strip():
            self.err(f"{path}.prompt", "an agent node needs a prompt")
        if sess == "main":
            for k in ("agent", "cwd"):
                if k in body:
                    self.err(
                        f"{path}.{k}",
                        f"{k} does not go with session: main: the main agent runs it",
                    )
        elif body.get("agent") not in self.agents:
            self.err(path, f"no agent {body.get('agent')} in .aegis.yaml")
        out = dict(body)
        if "result" in body:
            out["result"] = self.schema(body["result"], f"{path}.result")
        return out

    def schema(self, s, path: str, seen: tuple[str, ...] = ()) -> dict:
        """The schema with every ``$ref`` to a def resolved; errors at
        their paths, and an empty schema where one is refused."""
        if isinstance(s, dict) and "$ref" in s:
            ref = s["$ref"]
            if len(s) != 1 or not isinstance(ref, str) or not ref.startswith("#/defs/"):
                self.err(
                    f"{path}.$ref",
                    '$ref is alone in its mapping and reads "#/defs/<name>"',
                )
                return {}
            name = ref.removeprefix("#/defs/")
            if name not in self.defs:
                self.err(f"{path}.$ref", f"no def {name} in defs")
                return {}
            if name in seen:
                self.err(f"{path}.$ref", f"def {name} refers to itself")
                return {}
            return self.schema(self.defs[name], f"$.defs.{name}", (*seen, name))
        if not isinstance(s, dict):
            self.err(path, "a result schema is a mapping")
            return {}
        out = dict(s)
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
                out[k] = {
                    name: self.schema(sub, f"{path}.properties.{name}", seen)
                    for name, sub in v.items()
                }
            elif k == "items":
                out[k] = self.schema(v, f"{path}.items", seen)
        return out

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
        nodes = {n.key: n for n in walk(root)}
        parent = {c.key: n for n in nodes.values() for c in n.children}
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
            while n.key in parent:
                n = parent[n.key]
                out.append(n)
            return out

        def hidden(name: str, waiting: bool) -> str:
            """Why ``name`` is not seen from a node that cannot find it: the
            map or loop it is inside, or "" when no node has that id."""
            inside = next((x for x in nodes.values() if x.id == name), None)
            if inside is None or not inside.maps:
                return ""
            m = inside.maps[-1]
            if nodes["/".join(inside.maps)].kind == "loop":
                if waiting:
                    return f"{name} is inside loop {m}; wait on {m}"
                return f"{name} is inside loop {m}; read {m}.{name}"
            return f"{name} is inside map {m}; wait on {m} or one of its items"

        def branch_of(t: Node) -> tuple[Node, Node] | None:
            """The innermost if whose branch holds t, and that branch."""
            x = t
            while x.key in parent:
                p = parent[x.key]
                if p.kind == "if":
                    return p, x
                x = p
            return None

        def unreachable(t: Node, n: Node) -> str | None:
            """Why n may not read t: t sits in a branch that n is not inside."""
            got = branch_of(t)
            while got is not None:
                i, b = got
                if b.key not in {x.key for x in [n, *ancestors(n)]}:
                    return f"{t.id} is inside the {b.branch} branch of if {i.id}, which may not run: read it inside that branch"
                got = branch_of(i)
            return None

        for n in nodes.values():
            edge(("S", n.key), ("F", n.key))
            for c in n.children:
                edge(("S", n.key), ("S", c.key))
                edge(("F", c.key), ("F", n.key))
            if n.kind == "seq":
                for a, b in zip(n.children, n.children[1:]):
                    edge(("F", a.key), ("S", b.key))
        waited: dict[str, list[str]] = {}
        for n in nodes.values():
            for i, a in enumerate(n.after):
                path = f"{n.path}.after[{i}]"
                target, _, sub = a.partition(".")
                t = find(nodes, target, n.maps)
                if t is None and (why := hidden(target, True)):
                    self.err(path, why)
                    continue
                if t is None:
                    self.err(path, f"{target} is not a node")
                    continue
                if (why := unreachable(t, n)) is not None:
                    self.err(path, why)
                    continue
                if t.key == n.key or t.key in {x.key for x in ancestors(n)}:
                    self.err(
                        path, f"{target} contains this node, so it cannot finish first"
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
                    point = ("S", t.key)
                elif sub:
                    point = ("F", t.children[0].key)
                else:
                    point = ("F", t.key)
                if reach(("S", n.key), point):
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
                edge(point, ("S", n.key))
                if sub and t.kind == "agent":
                    wanted = waited.setdefault(t.key, [])
                    if sub not in wanted:
                        wanted.append(sub)
        ids = {x.id for x in nodes.values()}
        for m in nodes.values():
            if m.kind == "map" and m.body.get("as") in ids:
                self.err(
                    f"{m.path}.map.as",
                    f"{m.body['as']} is a node id; name the item otherwise",
                )
        for x in nodes.values():
            for path, tree in self._exprs(x):
                # A loop's until is read once its do finishes: by the do,
                # which may read anything inside itself.
                n, done = x, set()
                if x.kind == "loop" and path.startswith(x.path + ".loop.until"):
                    if not x.children:
                        continue  # its do was refused, with its own error
                    n = x.children[0]
                    done = {d.key for d in walk(n)}
                afters = {a for y in [n, *ancestors(n)] for a in y.after}
                # The enclosing maps' items.
                items = {
                    nodes["/".join(n.maps[: i + 1])].body.get("as")
                    for i in range(len(n.maps))
                }
                for r in refs(tree):
                    name, first = r.name, (r.parts or ("",))[0]
                    if name == "args":
                        continue
                    if name in items:
                        continue  # an enclosing map's item; the innermost wins
                    t = find(nodes, name, n.maps)
                    if t is None and (why := hidden(name, False)):
                        self.err(path, why)
                    elif t is None:
                        self.err(path, f"{name} is not an earlier node or args")
                    elif (why := unreachable(t, n)) is not None:
                        self.err(path, why)
                    elif t.key in done:
                        continue
                    elif not (
                        reach(("F", t.key), ("S", n.key)) or f"{name}.{first}" in afters
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
        t_up = {x.key for x in [t, *ancestors(t)]}
        for i, p in enumerate(up[1:], 1):
            branch = up[i - 1]
            if (
                p.kind == "par"
                and p.body.get("cap", len(p.children)) < len(p.children)
                and branch is not n
                and branch.key not in t_up
                and p.key in t_up
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
    def _probe_path(n: Node) -> str | None:
        """The YAML path of a node's probe, when it has one."""
        if not n.body.get("probe"):
            return None
        return (
            n.path
            + {"check": ".check", "if": ".if.when", "loop": ".loop.until"}[n.kind]
        )

    @staticmethod
    def _bare(n: Node) -> list[tuple[str, str]]:
        """The bare expressions a node holds: an expr probe, a judge's inputs."""
        probe, at = n.body.get("probe") or {}, _Parser._probe_path(n)
        if "expr" in probe:
            return [(f"{at}.expr", probe["expr"])]
        if "judge" in probe:
            return [(f"{at}.judge.inputs", i) for i in probe["judge"]["inputs"]]
        if n.kind == "gate":
            return [(f"{n.path}.gate.show", s) for s in n.body.get("show") or []]
        return []

    @staticmethod
    def _strings(n: Node) -> list[tuple[str, str]]:
        out = [(n.path + ".say", n.say)]
        if n.see:
            out.append((n.path + ".see", n.see))
        for k in ("prompt", "cwd"):
            if n.kind == "agent" and isinstance(n.body.get(k), str):
                out.append((f"{n.path}.agent.{k}", n.body[k]))
        if n.kind == "map" and isinstance(n.body.get("over"), str):
            out.append((n.path + ".map.over", n.body["over"]))
        if n.kind == "sh" and isinstance(n.body.get("sh"), str):
            out.append((n.path + ".sh", n.body["sh"]))
        if n.kind == "wait" and isinstance(n.body.get("wait"), str):
            out.append((n.path + ".wait", n.body["wait"]))
        if n.kind == "loop" and isinstance(n.body.get("max"), str):
            out.append((n.path + ".loop.max", n.body["max"]))
        if n.kind == "gate" and isinstance(n.body.get("ask"), str):
            out.append((n.path + ".gate.ask", n.body["ask"]))
        probe, at = n.body.get("probe") or {}, _Parser._probe_path(n)
        if "sh" in probe:
            out.append((f"{at}.sh", probe["sh"]))
        if "judge" in probe and isinstance(probe["judge"]["prompt"], str):
            out.append((f"{at}.judge.prompt", probe["judge"]["prompt"]))
        return out


def parse(text: str, agents: set[str]) -> tuple[Workflow | None, list[LintError]]:
    try:
        return _parse(text, agents)
    except RecursionError:  # an agent's text never crashes the lint
        return None, [LintError("$", "the workflow is nested too deep")]


def _parse(text: str, agents: set[str]) -> tuple[Workflow | None, list[LintError]]:
    try:
        raw = YAML(typ="safe").load(io.StringIO(text))
    except YAMLError as e:
        return None, [LintError("$", f"the YAML does not parse: {e}")]
    # Measured without recursion, so the limit is the same on any stack:
    # four YAML levels per node is more than any kind takes.
    todo, deepest = [(raw, 1)], 0
    while todo:
        x, d = todo.pop()
        deepest = max(deepest, d)
        if isinstance(x, dict | list) and deepest <= 4 * MAX_NESTING:
            todo += [(v, d + 1) for v in (x.values() if isinstance(x, dict) else x)]
    if deepest > 4 * MAX_NESTING:
        return None, [LintError("$", "the workflow is nested too deep")]
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
            "defs",
            "root",
        ):
            p.err(f"$.{k}", f"{k} is not a workflow field")
    # Blocks are expanded where a ref uses them, so they are read first.
    blocks = raw.get("blocks") or {}
    if not isinstance(blocks, dict):
        p.err("$.blocks", "blocks maps a name to a node")
        blocks = {}
    for name, b in blocks.items():
        if not isinstance(name, str) or not ID.match(name):
            p.err(
                f"$.blocks.{name}",
                "a block's name is lowercase letters, digits, - and _",
            )
        elif not isinstance(b, dict):
            p.err(f"$.blocks.{name}", "a block is a node: a mapping with one kind key")
    p.blocks = blocks
    defs = raw.get("defs") or {}
    if not isinstance(defs, dict):
        p.err("$.defs", "defs maps a name to a result schema")
        defs = {}
    p.defs = defs
    resolved = {
        name: p.schema(d, f"$.defs.{name}", (name,)) for name, d in defs.items()
    }
    deadline = None
    if "deadline" in raw:
        deadline = duration(raw["deadline"])
        if deadline is None:
            p.err("$.deadline", "a deadline is a duration such as 20m or 1h30m")
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
    for name in blocks:
        if name not in p.used and isinstance(blocks[name], dict):
            p.err(f"$.blocks.{name}", f"block {name} is never used")
    waited: dict[str, list[str]] = {}
    if root is not None:
        waited = p.order(root)
    for path, v in p.caps:
        if "total" in limits and v > limits["total"]:
            p.err(path, f"cap {v} is above limits.total {limits['total']}")
    # A block's error reads once, not once per use.
    errors = list(dict.fromkeys(p.errors))
    if errors or root is None:
        return None, errors
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
            deadline=deadline,
            by_key={n.key: n for n in walk(root)},
            defs=resolved,
            parent={c.key: n for n in walk(root) for c in n.children},
        ),
        [],
    )


# -- live edits (spec §06) ------------------------------------------------------
def _fields(n: Node) -> dict:
    """What a node does, apart from its children: what an edit compares."""
    return {
        "kind": n.kind,
        "say": n.say,
        "body": n.body,
        "after": n.after,
        "deadline": n.deadline,
        "retries": n.retries,
        "branch": n.branch,
        "see": n.see,
    }


GROUP_FIXED = {"map": ("over", "as"), "loop": ("max", "probe"), "if": ("probe",)}
# How the diff card names a changed part of a node's body.
PART = {
    "prompt": "its prompt",
    "sh": "its command",
    "result": "its schema",
    "ask": "its question",
    "agent": "its agent",
    "over": "its items",
    "probe": "its probe",
    "max": "its rounds",
    "wait": "its wait",
    "show": "what it shows",
    "by": "who decides",
}
HEADER = ("title", "say", "limits", "deadline", "defs")


def edit_errors(old: Workflow, new: Workflow, seen: dict[str, str]) -> list[LintError]:
    """The rules a live edit adds to the lint (spec §06): nothing that has
    started is removed, moved or changed, except a failed or stopped leaf;
    a begun seq takes new steps only after its started ones; a group that
    finished takes none. ``seen`` maps a template key to running, ran,
    parked (a failed or stopped leaf), begun or finished (a group). A begun
    seq also keeps the order of the steps it had."""
    out: list[LintError] = []
    if new.id != old.id:
        out.append(LintError("$.id", f"a live edit keeps the workflow's id, {old.id}"))
    if new.args != old.args:
        out.append(
            LintError(
                "$.args",
                "a live edit keeps args: they were given when the run was armed",
            )
        )
    for key, how in seen.items():
        o = old.by_key.get(key)
        if o is None:
            continue
        n = new.by_key.get(key)
        hint = (
            f" (it has the generated id {o.id}: write id: {o.id} on it, and give "
            "new steps ids of their own)"
            if re.fullmatch(r"n\d+", o.id)
            else ""
        )
        if n is None:
            out.append(
                LintError(
                    o.path,
                    f"{o.say!r} has started, so it stays; skip it with "
                    f"workflow_answer instead{hint}",
                )
            )
            continue
        if o.generated and n.generated and _fields(n) != _fields(o):
            # A step inserted before it renumbers the rest: the same id
            # may now name another step, which would take this one's state.
            out.append(
                LintError(
                    n.path,
                    f"{o.say!r} has started and its id {o.id} was generated, so "
                    f"an edit may not change what that id names{hint}",
                )
            )
            continue
        if n.kind != o.kind:
            out.append(
                LintError(
                    n.path, f"{o.say!r} has started as a {o.kind}; it stays one{hint}"
                )
            )
            continue
        op, np_ = old.parent.get(key), new.parent.get(key)
        if (op.key if op else None) != (np_.key if np_ else None):
            out.append(
                LintError(
                    n.path, f"{o.say!r} has started, so it stays where it is{hint}"
                )
            )
        if not o.children:
            if _fields(n) != _fields(o):
                if how == "running":
                    out.append(
                        LintError(
                            n.path,
                            f"{o.say!r} is running: stop it with workflow_stop and "
                            "its node, then change it",
                        )
                    )
                elif how == "ran":
                    out.append(
                        LintError(
                            n.path,
                            f"{o.say!r} has run and keeps its result; add a new "
                            "step instead",
                        )
                    )
            continue
        for f in GROUP_FIXED.get(o.kind, ()):
            if n.body.get(f) != o.body.get(f):
                word = {"loop": "until", "if": "when"}[o.kind] if f == "probe" else f
                out.append(LintError(n.path, f"a started {o.kind} keeps its {word}"))
        kids_o, kids_n = [c.key for c in o.children], [c.key for c in n.children]
        added = [k for k in kids_n if k not in kids_o]
        if o.kind == "seq" and [k for k in kids_o if k in kids_n] != [
            k for k in kids_n if k in kids_o
        ]:
            # A step moved before one that started would start beside it.
            out.append(
                LintError(
                    n.path,
                    f"{o.id} has begun, so it keeps the order of its steps; "
                    "add new steps instead of moving these",
                )
            )
        if how == "finished" and added:
            out.append(
                LintError(n.path, f"{o.id} has finished; put the new step after it")
            )
        elif o.kind == "seq" and added:
            started = [k for k in kids_n if k in seen]
            last = max((kids_n.index(k) for k in started), default=-1)
            for k in added:
                if kids_n.index(k) < last:
                    out.append(
                        LintError(
                            new.by_key[k].path,
                            f"a new step cannot go before "
                            f"{new.by_key[started[-1]].id}, which has started",
                        )
                    )
    return out


def _parts(o: Node, n: Node) -> list[str]:
    """The parts of a node an edit changed, as the diff card names them."""
    keys = sorted(set(o.body) | set(n.body))
    out = ["its kind"] if o.kind != n.kind else []
    out += [
        # A gate's decider is named: "who decides" alone hides who now does.
        f"who decides: {n.body[k]}"
        if k == "by" and n.body.get(k)
        else PART.get(k, f"its {k}")
        for k in keys
        if o.body.get(k) != n.body.get(k)
    ]
    fs = ("after", "deadline", "retries", "see")
    return out + [f for f in fs if getattr(o, f) != getattr(n, f)]


def edit_diff(old: Workflow, new: Workflow) -> dict:
    """An edit as the person reads it: each step added, changed or removed,
    by its sentence (a step moved to another group unchanged is a move), in
    the new tree's order and then the removed ones at
    their old depth; the keys it touches, with everything under them; and
    the shell commands and sh probes it adds or changes. Each line shows its
    step's command or sh probe."""
    diff: list[dict] = []
    header = [f"its {k}" for k in HEADER if getattr(old, k) != getattr(new, k)]
    if header:
        diff.append(
            {
                "op": "change",
                "key": "$",
                "say": new.say,
                "fields": header,
                "kind": "",
                "depth": 0,
            }
        )

    def depths(root: Node) -> dict[str, int]:
        out: dict[str, int] = {}

        def go(n: Node, d: int) -> None:
            out[n.key] = d
            for c in n.children:
                go(c, d + 1)

        go(root, 0)
        return out

    dn, do_ = depths(new.root), depths(old.root)
    touched: list[str] = []
    commands = {"sh": 0, "probes": 0}
    for n in walk(new.root):
        o = old.by_key.get(n.key)
        op_, np_ = old.parent.get(n.key), new.parent.get(n.key)
        moved = o is not None and (op_.key if op_ else None) != (
            np_.key if np_ else None
        )
        same = o is not None and _fields(o) == _fields(n)
        if same and not moved:
            continue
        d: dict = {
            "op": "add" if o is None else "move" if same else "change",
            "key": n.key,
            "say": n.say,
            "kind": n.kind,
            "depth": dn[n.key],
        }
        if o is not None:
            d["fields"] = _parts(o, n) + (["its place"] if moved else [])
            if o.say != n.say:
                d["was"] = o.say
        # Every command on a line is shown; one is counted only when its
        # text is new or changed, since the person approved the rest.
        was = o.body if o is not None and o.kind == n.kind else {}
        if n.kind == "sh":
            d["cmd"] = n.body.get("sh", "")
            commands["sh"] += d["cmd"] != was.get("sh")
        if "sh" in (probe := n.body.get("probe") or {}):
            d["cmd"] = probe["sh"]
            commands["probes"] += probe["sh"] != (was.get("probe") or {}).get("sh")
        diff.append(d)
        touched += [x.key for x in walk(n) if x.key not in touched]
    for o in walk(old.root):
        if o.key not in new.by_key:
            diff.append(
                {
                    "op": "remove",
                    "key": o.key,
                    "say": o.say,
                    "kind": o.kind,
                    "depth": do_[o.key],
                }
            )
            touched += [o.key] if o.key not in touched else []
    return {"diff": diff, "touched": touched, "commands": commands}
