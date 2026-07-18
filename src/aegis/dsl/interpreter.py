from __future__ import annotations

import json
from typing import Any

from aegis.dsl.models import Spec
from aegis.dsl.refs import Store, resolve_selector, substitute
from aegis.workflow import workflow
from aegis.workflow.decorator import WorkflowError

_SCHEMA_HINT = (
    "\n\nReturn ONLY a JSON object matching this JSON Schema, no prose:\n{schema}")


class Interpreter:
    def __init__(self, engine, *, args: dict, default_profile: str | None):
        self.engine = engine
        self.args = args or {}
        self.default_profile = default_profile
        self.store = Store()

    async def run_node(self, node, *, path: str, scope: dict) -> Any:
        if path in self.store.outputs:
            return self.store.outputs[path]           # replay — do not re-run
        if node.type == "sequence":
            return await self._run_sequence(node, path=path, scope=scope)
        if node.type == "agent":
            out = await self._run_agent(node, path=path, scope=scope)
            await self._checkpoint()
            return out
        raise NotImplementedError(f"node type not supported yet: {node.type}")

    async def _checkpoint(self) -> None:
        try:
            await self.engine.checkpoint("dsl", self.store.snapshot())
        except RuntimeError:
            pass  # no runner/state_dir — durability is opt-in

    async def _run_sequence(self, node, *, path, scope) -> dict:
        out: dict[str, Any] = {}
        for i, child in enumerate(node.children):
            cout = await self.run_node(
                child, path=f"{path}.{i}", scope=scope)
            if child.id:
                out[child.id] = cout
        return out

    async def _run_agent(self, node, *, path, scope) -> Any:
        bindings = self._bindings(node, scope)
        prompt = substitute(node.prompt, bindings)
        if node.schema_ is not None:
            prompt = prompt + _SCHEMA_HINT.format(
                schema=json.dumps(node.schema_))
        profile = self._profile_of(node)
        handle = await self.engine.spawn(profile)
        try:
            reply = await self.engine.send(handle, prompt)
            output = await self._coerce(node, handle, reply)
        finally:
            await self.engine.close(handle)
        self.store.record(path, node.id, output)
        return output

    def _bindings(self, node, scope) -> dict:
        b: dict[str, Any] = {"args": self.args}
        b.update(scope)  # item / index inside map bodies (slice 3)
        for name, selector in node.inputs.items():
            b[name] = resolve_selector(selector, self.store)
        return b

    async def _coerce(self, node, handle, reply):
        if node.schema_ is None:
            return reply
        from jsonschema import Draft202012Validator
        validator = Draft202012Validator(node.schema_)
        last_err: Exception | None = None
        for attempt in range(2):
            try:
                parsed = json.loads(_extract_json(reply))
                validator.validate(parsed)
                return parsed
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt == 1:
                    break
                reply = await self.engine.send(
                    handle,
                    "Your last reply was not valid JSON for the schema. "
                    f"Return ONLY the JSON object. Error: {e}")
        raise WorkflowError(
            f"agent {node.id!r} did not return schema-valid JSON "
            f"after retry: {last_err}")

    def _profile_of(self, node) -> str:
        if node.target is not None:
            return node.target.profile
        if self.default_profile is None:
            raise ValueError(
                f"agent node {node.id!r} has no target and no "
                "default_profile is configured")
        return self.default_profile


def _extract_json(text: str) -> str:
    """Pull the outermost {...} or [...] block from a possibly-chatty reply."""
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            return text[start:end + 1]
    return text


@workflow("dynamic")
async def dynamic(engine, *, spec, kwargs=None, default_profile=None):
    model = spec if isinstance(spec, Spec) else Spec.model_validate(spec)
    interp = Interpreter(
        engine, args=kwargs or {}, default_profile=default_profile)
    try:
        snap = await engine.resume_state()
    except RuntimeError:
        snap = None
    if snap:
        interp.store.load(snap)
    return await interp.run_node(model.root, path="root", scope={})
