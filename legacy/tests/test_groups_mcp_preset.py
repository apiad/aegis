from __future__ import annotations

import pytest

from aegis.mcp.server import _aegis_group_spawn_mixed_impl


class _GroupsStub:
    def __init__(self):
        self.calls = []

    async def spawn_mixed(self, *, group, profiles):
        self.calls.append((group, list(profiles)))
        return [f"h-{i}" for i, _ in enumerate(profiles)]


class _BridgeStub:
    def __init__(self, *, presets=None):
        self.groups = _GroupsStub()
        self.config = {"groups": {"presets": presets or {}}}


@pytest.mark.asyncio
async def test_spawn_mixed_resolves_preset():
    br = _BridgeStub(presets={"code_audit":
                              {"profiles": ["sec", "style", "logic"]}})
    out = await _aegis_group_spawn_mixed_impl(
        br, group="rev", preset="code_audit")
    assert out["group"] == "rev"
    assert out["handles"] == ["h-0", "h-1", "h-2"]
    assert br.groups.calls == [("rev", ["sec", "style", "logic"])]


@pytest.mark.asyncio
async def test_spawn_mixed_inline_profiles_still_supported():
    br = _BridgeStub()
    out = await _aegis_group_spawn_mixed_impl(
        br, group="rev", profiles=["a", "b"])
    assert out["handles"] == ["h-0", "h-1"]
    assert br.groups.calls == [("rev", ["a", "b"])]


@pytest.mark.asyncio
async def test_spawn_mixed_requires_profiles_or_preset():
    br = _BridgeStub()
    with pytest.raises(ValueError):
        await _aegis_group_spawn_mixed_impl(br, group="rev")


@pytest.mark.asyncio
async def test_spawn_mixed_unknown_preset_raises():
    br = _BridgeStub(presets={"x": {"profiles": ["y"]}})
    with pytest.raises(KeyError):
        await _aegis_group_spawn_mixed_impl(
            br, group="rev", preset="missing")
