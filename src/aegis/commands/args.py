"""Declarative argument parsing for slash commands.

A command declares an ``ArgSpec`` (positionals + flags); ``parse`` turns the
raw argument string into a validated ``Args``. Parsing rule: ``--flag`` tokens
are recognized anywhere among the non-greedy positionals (so a boolean flag
may lead or trail), while positionals bind in order. A trailing ``greedy``
positional stops flag parsing and takes the raw, un-tokenized remainder, so
free-text (prompts) survives verbatim — including any ``--x`` inside it.
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

    def _consume_flag(s: str) -> "tuple[bool, str]":
        """If ``s`` starts with a recognized ``--flag``, consume it (and its
        value) into ``flag_values`` and return ``(True, remainder)``. A
        leading ``--`` that names no declared flag raises. Otherwise
        ``(False, s)``."""
        token, rest = _pop_token(s)
        if token is None or not token.startswith("--"):
            return False, s
        name = token[2:]
        inline = None
        if "=" in name:
            name, inline = name.split("=", 1)
        f = flags_by_name.get(name)
        if f is None:
            raise ArgError(f"unknown flag: --{name}")
        if not f.takes_value:
            flag_values[name] = True
            return True, rest
        if inline is not None:
            flag_values[name] = inline
            return True, rest
        value, rest2 = _pop_token(rest)
        if value is None:
            raise ArgError(f"flag --{name} needs a value")
        flag_values[name] = value
        return True, rest2

    positionals = list(spec.positionals)
    positional: dict = {}
    s = argstr
    i = 0

    # Interleave flags + non-greedy positionals, left to right.
    while i < len(positionals) and not positionals[i].greedy:
        consumed, s = _consume_flag(s)
        if consumed:
            continue
        p = positionals[i]
        token, rest = _pop_token(s)
        if token is None:
            if p.required:
                raise ArgError(f"missing required argument: {p.name}")
            i += 1
            continue
        positional[p.name] = token
        s = rest
        i += 1

    # Flags may sit between the last non-greedy positional and the greedy
    # region (or trail a spec that has no greedy positional at all).
    while True:
        consumed, s = _consume_flag(s)
        if not consumed:
            break

    # Greedy positional (if any) takes the raw remainder verbatim.
    if i < len(positionals) and positionals[i].greedy:
        p = positionals[i]
        value = s.strip()
        if value:
            positional[p.name] = value
        elif p.required:
            raise ArgError(f"missing required argument: {p.name}")
        s = ""

    if s.strip():
        raise ArgError(f"unexpected extra arguments: {s.strip()}")

    return Args(positional=positional, flags=flag_values)
