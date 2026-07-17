"""Declarative argument parsing for slash commands.

A command declares an ``ArgSpec`` (positionals + flags); ``parse`` turns the
raw argument string into a validated ``Args``. Parsing rule: *flags lead* —
``--flag`` tokens are consumed from the front while they name a declared
flag, then positionals bind in order. A trailing ``greedy`` positional takes
the raw, un-tokenized remainder so free-text (prompts) survives verbatim.
Pure: no registry, no UI.
"""
from __future__ import annotations

from dataclasses import dataclass


class ArgError(ValueError):
    """Human-facing argument parse error (message is shown to the user)."""


@dataclass(frozen=True)
class Arg:
    name: str
    required: bool = True
    greedy: bool = False          # last positional only; takes raw remainder


@dataclass(frozen=True)
class Flag:
    name: str                     # "effort" matches --effort
    takes_value: bool = True      # False → boolean presence flag
    default: "str | bool | None" = None


@dataclass(frozen=True)
class ArgSpec:
    positionals: tuple[Arg, ...] = ()
    flags: tuple[Flag, ...] = ()


@dataclass(frozen=True)
class Args:
    positional: dict
    flags: dict

    def __getitem__(self, key):
        if key in self.positional:
            return self.positional[key]
        return self.flags[key]

    def get(self, key, default=None):
        if key in self.positional:
            return self.positional[key]
        return self.flags.get(key, default)


def _pop_token(s: str) -> "tuple[str | None, str]":
    """Pop one whitespace-delimited token from the front of ``s``, honoring
    single/double quotes. Returns ``(token, remainder)``; ``token`` is None
    when ``s`` is blank. Raises ArgError on an unterminated quote."""
    s = s.lstrip()
    if not s:
        return None, ""
    out: list[str] = []
    quote: str | None = None
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if quote:
            if c == quote:
                quote = None
            else:
                out.append(c)
        elif c in ('"', "'"):
            quote = c
        elif c.isspace():
            break
        else:
            out.append(c)
        i += 1
    if quote:
        raise ArgError("unterminated quote")
    return "".join(out), s[i:].lstrip()


def parse(spec: ArgSpec, argstr: str) -> Args:
    flags_by_name = {f.name: f for f in spec.flags}
    flag_values: dict = {}
    for f in spec.flags:
        flag_values[f.name] = (
            f.default if f.default is not None
            else (False if not f.takes_value else None))

    s = argstr
    # --- flag run (flags lead) ---
    while True:
        token, rest = _pop_token(s)
        if token is None or not token.startswith("--"):
            break
        name = token[2:]
        inline = None
        if "=" in name:
            name, inline = name.split("=", 1)
        f = flags_by_name.get(name)
        if f is None:
            raise ArgError(f"unknown flag: --{name}")
        if not f.takes_value:
            flag_values[name] = True
            s = rest
            continue
        if inline is not None:
            flag_values[name] = inline
            s = rest
            continue
        value, rest2 = _pop_token(rest)
        if value is None:
            raise ArgError(f"flag --{name} needs a value")
        flag_values[name] = value
        s = rest2

    # --- positionals ---
    positional: dict = {}
    for p in spec.positionals:
        if p.greedy:
            value = s.strip()
            if value:
                positional[p.name] = value
            elif p.required:
                raise ArgError(f"missing required argument: {p.name}")
            s = ""
            continue
        token, rest = _pop_token(s)
        if token is None:
            if p.required:
                raise ArgError(f"missing required argument: {p.name}")
            continue
        positional[p.name] = token
        s = rest

    if s.strip():
        raise ArgError(f"unexpected extra arguments: {s.strip()}")

    return Args(positional=positional, flags=flag_values)
