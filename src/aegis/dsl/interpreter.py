from __future__ import annotations

from typing import Any

from aegis.dsl.models import Spec
from aegis.workflow import workflow


class Interpreter:
    def __init__(self, engine, *, args: dict, default_profile: str | None):
        self.engine = engine
        self.args = args or {}
        self.default_profile = default_profile

    async def run_node(self, node, *, path: str, scope: dict) -> Any:
        if node.type == "sequence":
            return await self._run_sequence(node, path=path, scope=scope)
        if node.type == "agent":
            return await self._run_agent(node, path=path, scope=scope)
        raise NotImplementedError(f"node type not supported yet: {node.type}")

    async def _run_sequence(self, node, *, path, scope) -> dict:
        out: dict[str, Any] = {}
        for i, child in enumerate(node.children):
            cout = await self.run_node(
                child, path=f"{path}.{i}", scope=scope)
            if child.id:
                out[child.id] = cout
        return out

    async def _run_agent(self, node, *, path, scope) -> Any:
        profile = self._profile_of(node)
        handle = await self.engine.spawn(profile)
        try:
            reply = await self.engine.send(handle, node.prompt)
        finally:
            await self.engine.close(handle)
        return reply

    def _profile_of(self, node) -> str:
        if node.target is not None:
            return node.target.profile
        if self.default_profile is None:
            raise ValueError(
                f"agent node {node.id!r} has no target and no "
                "default_profile is configured")
        return self.default_profile


@workflow("dynamic")
async def dynamic(engine, *, spec, kwargs=None, default_profile=None):
    model = spec if isinstance(spec, Spec) else Spec.model_validate(spec)
    interp = Interpreter(
        engine, args=kwargs or {}, default_profile=default_profile)
    return await interp.run_node(model.root, path="root", scope={})
