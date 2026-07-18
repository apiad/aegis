from __future__ import annotations

from aegis.dsl.models import Spec


class DslValidationError(Exception):
    """Semantic validation failed (references, ids, profiles/queues)."""


# Reserved names that are always in scope inside certain body contexts
# (e.g. map body binds {{item}}/{{index}}); selectors whose head is one
# of these are not treated as node-id references during validation.
_SCOPE_RESERVED: frozenset[str] = frozenset({"item", "index", "args"})


def validate(spec: Spec, *, agents: set[str], queues: set[str],
             default_agent: str | None) -> None:
    seen_ids: set[str] = set()
    _walk(spec.root, seen_ids, agents=agents, queues=queues,
          default_agent=default_agent, scope_binds=frozenset())


def _walk(node, seen_ids, *, agents, queues, default_agent,
          scope_binds: frozenset[str]) -> None:
    t = node.type
    if t == "sequence":
        for child in node.children:
            _walk(child, seen_ids, agents=agents, queues=queues,
                  default_agent=default_agent, scope_binds=scope_binds)
        if node.id:
            _add_id(node.id, seen_ids)
        return
    if t == "parallel":
        for child in node.children:
            _walk(child, seen_ids, agents=agents, queues=queues,
                  default_agent=default_agent, scope_binds=scope_binds)
        if node.id:
            _add_id(node.id, seen_ids)
        return
    if t == "map":
        _check_ref(node.over, seen_ids, scope_binds)
        body_scope = scope_binds | {"item", "index"}
        _walk(node.body, seen_ids, agents=agents, queues=queues,
              default_agent=default_agent, scope_binds=body_scope)
        _add_id(node.id, seen_ids)
        return
    if t == "agent":
        for selector in node.inputs.values():
            _check_ref(selector, seen_ids, scope_binds)
        _check_target(node, agents, queues, default_agent)
        if node.id:
            _add_id(node.id, seen_ids)
        return
    raise DslValidationError(f"unknown node type in validate: {t!r}")


def _add_id(node_id: str, seen_ids: set[str]) -> None:
    if node_id in seen_ids:
        raise DslValidationError(f"duplicate node id: {node_id!r}")
    seen_ids.add(node_id)


def _check_ref(selector: str, seen_ids: set[str],
               scope_binds: frozenset[str]) -> None:
    head = selector.split(".")[0]
    if head in _SCOPE_RESERVED or head in scope_binds:
        return
    if head not in seen_ids:
        raise DslValidationError(
            f"reference {selector!r} points at id {head!r} which is not a "
            "declared upstream node")


def _check_target(node, agents, queues, default_agent) -> None:
    target = node.target
    if target is None:
        if default_agent is None:
            raise DslValidationError(
                f"agent {node.id!r} omits target but no default_agent is set")
        return
    if target.kind == "spawn" and target.profile not in agents:
        raise DslValidationError(
            f"spawn.profile {target.profile!r} is not a configured agent")
    if getattr(target, "kind", None) == "queue" and target.queue not in queues:
        raise DslValidationError(
            f"queue.queue {target.queue!r} is not a configured queue")
    # session.handle deferred to runtime (spec § Validation).
