from __future__ import annotations

import asyncio
import json
from typing import Any

from aegis.dsl.models import Spec
from aegis.dsl.refs import Store, resolve_selector, substitute
from aegis.workflow import workflow
from aegis.workflow.decorator import WorkflowError

DEFAULT_CONCURRENCY = 8

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
        if node.type == "parallel":
            out = await self._run_parallel(node, path=path, scope=scope)
            if node.id:
                self.store.record(path, node.id, out)
            await self._checkpoint()
            return out
        if node.type == "map":
            out = await self._run_map(node, path=path, scope=scope)
            self.store.record(path, node.id, out)
            await self._checkpoint()
            return out
        if node.type == "loop":
            out = await self._run_loop(node, path=path, scope=scope)
            self.store.record(path, node.id, out)
            await self._checkpoint()
            return out
        if node.type == "if":
            out = await self._run_if(node, path=path, scope=scope)
            if node.id:
                self.store.record(path, node.id, out)
            await self._checkpoint()
            return out
        if node.type == "human":
            out = await self._run_human(node, path=path, scope=scope)
            if node.id:
                self.store.record(path, node.id, out)
            await self._checkpoint()
            return out
        if node.type == "agent":
            out = await self._run_agent(node, path=path, scope=scope)
            await self._checkpoint()
            return out
        raise NotImplementedError(f"node type not supported yet: {node.type}")

    async def _run_parallel(self, node, *, path, scope) -> dict:
        idx_children = list(enumerate(node.children))
        results = await self.engine.parallel([
            self.run_node(c, path=f"{path}.{i}", scope=scope)
            for i, c in idx_children])
        return {c.id: r for (i, c), r in zip(idx_children, results) if c.id}

    async def _run_map(self, node, *, path, scope) -> list:
        items = resolve_selector(node.over, self.store)
        if not isinstance(items, list):
            raise WorkflowError(
                f"map.over {node.over!r} did not resolve to a list")
        sem = asyncio.Semaphore(node.concurrency or DEFAULT_CONCURRENCY)

        async def _one(i, item):
            async with sem:
                child_scope = {**scope, "item": item, "index": i}
                return await self.run_node(
                    node.body, path=f"{path}#{i}", scope=child_scope)

        return list(await asyncio.gather(
            *[_one(i, it) for i, it in enumerate(items)]))

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

    async def _run_human(self, node, *, path, scope) -> Any:
        bindings: dict[str, Any] = {"args": self.args}
        bindings.update(scope)
        question = substitute(node.question, bindings)
        options: list[str] | None = None
        schema = node.schema_
        if schema and schema.get("type") == "string" and "enum" in schema:
            options = list(schema["enum"])
        reply = await self.engine.ask_human(question, options=options)
        if schema is None:
            return reply
        if options is not None:
            if reply not in options:
                raise WorkflowError(
                    f"human {node.id!r} reply {reply!r} not in enum {options}")
            return reply
        from jsonschema import Draft202012Validator
        parsed = json.loads(_extract_json(reply))
        Draft202012Validator(schema).validate(parsed)
        return parsed

    async def _run_loop(self, node, *, path, scope) -> list:
        rounds: list[Any] = []
        for n in range(node.max_rounds):
            round_path = f"{path}#round{n}"
            body_out = await self.run_node(
                node.body, path=round_path, scope=scope)
            rounds.append(body_out)
            pred_key = f"{round_path}::pred"
            if pred_key in self.store.outputs:
                stop = bool(self.store.outputs[pred_key])
            else:
                stop = await self._eval_predicate(
                    node.until, path=round_path, scope=scope, last=body_out)
                self.store.outputs[pred_key] = stop
            await self._checkpoint()
            if stop:
                break
        return rounds

    async def _run_if(self, node, *, path, scope) -> Any:
        cond_key = f"{path}::cond"
        if cond_key in self.store.outputs:
            taken = bool(self.store.outputs[cond_key])
        else:
            taken = await self._eval_predicate(
                node.cond, path=path, scope=scope, last=None)
            self.store.outputs[cond_key] = taken
        await self._checkpoint()
        if taken:
            return await self.run_node(
                node.then, path=f"{path}.then", scope=scope)
        if node.else_ is not None:
            return await self.run_node(
                node.else_, path=f"{path}.else", scope=scope)
        return None

    async def _eval_predicate(self, pred, *, path, scope, last) -> bool:
        if pred.kind == "shell":
            res = await self.engine.bash(
                pred.cmd, cwd=pred.cwd, timeout=pred.timeout)
            return res["exit"] == 0
        if pred.kind == "judge":
            return await self._run_judge(pred, path=path, scope=scope, last=last)
        raise NotImplementedError(f"predicate kind: {pred.kind}")

    async def _run_judge(self, pred, *, path, scope, last) -> bool:
        bindings: dict[str, Any] = {"args": self.args, "last": last}
        bindings.update(scope)
        for selector in pred.inputs:
            head = selector.split(".")[0]
            bindings[head] = resolve_selector(selector, self.store)
        rendered_inputs = {k: bindings[k] for k in
                           ({s.split(".")[0] for s in pred.inputs} or {"last"})}
        prompt = (
            f"Decide: {pred.condition}\n\n"
            f"Context: {json.dumps(rendered_inputs, default=str)}\n\n"
            'Return ONLY JSON: {"decision": true|false, "reason": "..."}')
        handle = await self.engine.spawn(self.default_profile)
        try:
            reply = await self.engine.send(handle, prompt)
            parsed = json.loads(_extract_json(reply))
            decision = bool(parsed.get("decision"))
        finally:
            await self.engine.close(handle)
        self.store.outputs[f"{path}::pred"] = decision
        return decision

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
