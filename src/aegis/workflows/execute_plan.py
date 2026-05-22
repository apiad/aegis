"""execute_plan — drive an implementer subagent through a plan markdown.

Parses ``## Slice N — title`` headings via ``plan_parser``, dispatches one
subagent per task, optionally verifies via ``bash_predicate``, and
checkpoints after each task so a killed run resumes at the next unfinished
task.
"""
from __future__ import annotations

from aegis.workflow import workflow
from aegis.workflows._lib.plan_parser import parse_plan


def _task_prompt(task: dict) -> str:
    return (f"Task {task['id']}: {task['title']}\n\n{task['body']}").strip()


@workflow("execute_plan")
async def execute_plan(engine, *, plan_path: str) -> str:
    state = await engine.resume_state() or {"phase": "init", "done": []}

    if state["phase"] == "init":
        plan = parse_plan(plan_path)
        state = {"phase": "tasks", "plan_path": plan_path,
                 "tasks": [{"id": t.id, "title": t.title, "body": t.body}
                           for t in plan.tasks],
                 "done": []}
        await engine.checkpoint("parsed", state)

    profile = engine.config.get("default_subagent_profile", "implementer")
    for task in state["tasks"]:
        if task["id"] in state["done"]:
            continue
        engine.log(f"\u25b6 task {task['id']}: {task['title']}")
        impl = await engine.spawn(profile, alias=f"impl-{task['id']}")
        try:
            await engine.send(impl, _task_prompt(task))
            if "verify" in task:
                await engine.bash_predicate(
                    task["verify"],
                    retry_with=(f"Verification failed for task {task['id']}. "
                                "Output:\n{stdout}\n{stderr}\nPlease fix."),
                    max_retries=2)
        finally:
            await engine.close(impl)
        state["done"].append(task["id"])
        await engine.checkpoint(f"task_{task['id']}", state)

    return f"completed {len(state['done'])}/{len(state['tasks'])} tasks"
