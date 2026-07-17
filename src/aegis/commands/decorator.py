"""The @command plugin primitive — a control command contributed by a plugin.

Fourth decorator beside @workflow / @hook / @tool; auto-registered on the plugin
import sweep (:func:`aegis.config.yaml_loader.import_plugins`). Registers a
``source="plugin"`` SlashCommand, so :func:`register`'s precedence guard protects
builtins and user ``.md`` commands.
"""
from __future__ import annotations

import inspect

from aegis.commands import REGISTRY, SlashCommand, register
from aegis.commands.args import ArgSpec


def _usage_from_spec(name: str, spec: ArgSpec) -> str:
    parts = []
    for p in spec.positionals:
        parts.append(f"<{p.name}>" if p.required else f"[{p.name}]")
    return f"/{name}" + ("" if not parts else " " + " ".join(parts))


def _make(fn, *, name, summary, usage, spec):
    if not inspect.iscoroutinefunction(fn):
        raise TypeError(f"@command on {fn.__name__}: must be async def")
    params = list(inspect.signature(fn).parameters.values())
    if len(params) < 2 or params[0].name != "ctx" or params[1].name != "args":
        raise TypeError(
            f"@command on {fn.__name__}: signature must be (ctx, args)")
    n = name or fn.__name__
    s = spec or ArgSpec()
    summ = summary if summary is not None else (
        (fn.__doc__ or "").strip().splitlines()[0] if fn.__doc__ else "")
    use = usage or _usage_from_spec(n, s)
    # Idempotent reload: same-location re-registration replaces cleanly.
    existing = REGISTRY.get(n)
    if (existing is not None and existing.source == "plugin"
            and getattr(existing.run, "__code__", None) is not None
            and existing.run.__code__.co_filename == fn.__code__.co_filename
            and existing.run.__code__.co_firstlineno == fn.__code__.co_firstlineno):
        REGISTRY.pop(n, None)
    register(SlashCommand(n, summ, use, fn, source="plugin", spec=s))
    return fn


def command(fn=None, *, name=None, summary=None, usage=None, spec=None):
    """Register a plugin control command.

        @command
        async def ping(ctx, args): ...

        @command(name="pp", summary="…", usage="/pp <x>", spec=ArgSpec(...))
        async def _h(ctx, args): ...
    """
    if fn is not None:
        return _make(fn, name=name, summary=summary, usage=usage, spec=spec)

    def deco(f):
        return _make(f, name=name, summary=summary, usage=usage, spec=spec)
    return deco
