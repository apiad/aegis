"""The built-in loads by name and its defaults are complete."""

from __future__ import annotations

import importlib
import inspect
import re

import pytest


def test_both_workflows_register_on_import() -> None:
    """`workflows: [afk]` in .aegis.yaml imports the package by name; both
    workflows must land in the registry from that one import."""
    importlib.import_module("aegis.workflows.builtins.afk")
    from aegis.workflow import get_workflow

    assert get_workflow("afk") is not None
    assert get_workflow("afk_progress") is not None


def test_register_builtins_accepts_the_name() -> None:
    from aegis.config.yaml_loader import register_builtins

    register_builtins(type("Cfg", (), {"workflows": ["afk"]})())
    from aegis.workflow import get_workflow

    assert get_workflow("afk") is not None


@pytest.mark.asyncio
async def test_afk_refuses_without_required_args() -> None:
    """A schedule missing `owner` must fail loudly at the first fire, not
    quietly do nothing every ten minutes forever."""
    from aegis.workflows.builtins.afk import afk

    engine = type("E", (), {"config": {}})()
    with pytest.raises(ValueError, match="owner"):
        await afk(engine)


@pytest.mark.asyncio
async def test_afk_progress_refuses_without_required_args() -> None:
    from aegis.workflows.builtins.afk import afk_progress

    engine = type("E", (), {"config": {}})()
    with pytest.raises(ValueError, match="owner"):
        await afk_progress(engine)


# Only subscripts can raise. `cfg.get("field_names")` and friends are reads
# with a fallback beside them and are meant to be absent.
_SUBSCRIPT_RE = re.compile(r"""cfg\[\s*["']([A-Za-z_][A-Za-z0-9_]*)["']\s*\]""")

# Supplied per schedule rather than defaulted: there is no sensible default
# for whose board this is.
REQUIRED_ARGS = {"owner", "project", "repo_root"}


def _keys_read(module) -> set[str]:
    return set(_SUBSCRIPT_RE.findall(inspect.getsource(module)))


def test_defaults_cover_every_key_the_tick_reads() -> None:
    """A key either schedule reads but DEFAULTS omits is a KeyError at 3am on
    a machine nobody is watching.

    Both modules are scanned. `afk_progress` fires every two minutes and
    `afk` every ten, so a gap in the progress schedule is the one an operator
    hits first.
    """
    from aegis.workflows.builtins.afk import DEFAULTS, progress, tick

    read = _keys_read(tick) | _keys_read(progress)

    # The scan itself must be able to fail. If a rename or a refactor stops
    # the regex matching, `read` goes empty and the assertion below passes
    # vacuously, which is the one outcome this test must not have.
    assert {"max_in_flight", "gate_commands"} <= read, "tick scan found nothing"
    assert "stall_after_s" in read, "progress scan found nothing"

    missing = read - REQUIRED_ARGS - set(DEFAULTS)
    assert not missing, f"read from cfg but absent from DEFAULTS: {sorted(missing)}"


def test_required_args_are_not_silently_defaulted() -> None:
    """The three the workflows validate by hand must stay out of DEFAULTS.

    A default for `repo_root` would turn a misconfigured schedule into a
    worker dispatched into whatever tree that default happened to name.
    """
    from aegis.workflows.builtins.afk import DEFAULTS

    assert REQUIRED_ARGS.isdisjoint(DEFAULTS)


def test_notify_cmd_stays_absent() -> None:
    """Notification is not implemented. A key that is read, documented and
    does nothing is worse than an absent one: the first person to set it
    concludes the loop is broken."""
    from aegis.workflows.builtins.afk import DEFAULTS

    assert "notify_cmd" not in DEFAULTS
