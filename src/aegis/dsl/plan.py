from __future__ import annotations

from dataclasses import dataclass, field

from aegis.dsl.models import Spec


@dataclass
class PlanPreview:
    projected_agents: int = 0
    is_upper_bound: bool = False
    lines: list[str] = field(default_factory=list)

    def render(self) -> str:
        footer = f"Projected agents: {self.projected_agents}"
        if self.is_upper_bound:
            footer += " (upper bound)"
        return "\n".join(self.lines + [footer])


def build_plan(spec: Spec, *, kwargs: dict | None = None) -> PlanPreview:
    plan = PlanPreview()
    _visit(spec.root, plan, depth=0)
    return plan


def _visit(node, plan: PlanPreview, *, depth: int) -> int:
    """Recursively count projected agents; append a tree line; return
    the count contributed by this subtree."""
    indent = "  " * depth
    t = node.type
    if t == "agent":
        plan.lines.append(f"{indent}- agent({node.id or '_'})")
        plan.projected_agents += 1
        return 1
    if t == "human":
        plan.lines.append(f"{indent}- human({node.id or '_'})")
        return 0
    if t == "sequence":
        plan.lines.append(f"{indent}- sequence")
        total = 0
        for c in node.children:
            total += _visit(c, plan, depth=depth + 1)
        return total
    if t == "parallel":
        plan.lines.append(f"{indent}- parallel")
        total = 0
        for c in node.children:
            total += _visit(c, plan, depth=depth + 1)
        return total
    if t == "map":
        plan.lines.append(f"{indent}- map({node.id}) over {node.over}")
        plan.is_upper_bound = True
        return _visit(node.body, plan, depth=depth + 1)
    if t == "loop":
        plan.lines.append(
            f"{indent}- loop({node.id}) x{node.max_rounds}")
        plan.is_upper_bound = True
        # snapshot body count without double-counting during recursion
        before = plan.projected_agents
        body_count = _visit(node.body, plan, depth=depth + 1)
        plan.projected_agents = before + body_count * node.max_rounds
        pred_count = _predicate_count(node.until)
        plan.projected_agents += pred_count * node.max_rounds
        return body_count * node.max_rounds + pred_count * node.max_rounds
    if t == "if":
        plan.lines.append(f"{indent}- if({node.id or '_'})")
        pred_count = _predicate_count(node.cond)
        plan.projected_agents += pred_count
        total = pred_count
        total += _visit(node.then, plan, depth=depth + 1)
        if node.else_ is not None:
            total += _visit(node.else_, plan, depth=depth + 1)
        return total
    raise NotImplementedError(f"plan: unknown node type {t!r}")


def _predicate_count(pred) -> int:
    return 1 if pred.kind == "judge" else 0
