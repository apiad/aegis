from __future__ import annotations

from pathlib import Path
from typing import Any

from aegis.queue.schema import new_ulid
from aegis.workflow.decorator import (
    WorkflowError, get_workflow, list_workflows,
)
from aegis.workflow.engine import WorkflowEngine


async def run_workflow(
    name: str, kwargs: dict, *,
    bridge: Any, queue_manager: Any, inbox_router: Any,
    caller_handle: str | None = None,
    state_dir: Path | None = None,
) -> dict:
    """Build a WorkflowEngine, invoke the named workflow with kwargs,
    auto-drain touched handles + auto-close spawned handles in finally.
    Returns {status, result?, error?, workflow_run_id}.
    """
    run_id = new_ulid()
    fn = get_workflow(name)
    if fn is None:
        return {
            "status": "error",
            "error": (f"unknown workflow: {name!r}. "
                      f"Available: {list_workflows()}"),
            "workflow_run_id": run_id,
        }
    engine = WorkflowEngine(
        workflow_name=name, workflow_run_id=run_id,
        bridge=bridge, queue_manager=queue_manager,
        inbox_router=inbox_router,
        caller_handle=caller_handle, state_dir=state_dir)
    try:
        result = await fn(engine, **kwargs)
        return {"status": "ok", "result": result, "workflow_run_id": run_id}
    except WorkflowError as e:
        return {"status": "error", "error": str(e),
                "workflow_run_id": run_id}
    except Exception as e:  # noqa: BLE001 — unexpected crash → tagged
        import traceback
        traceback.print_exc()
        return {
            "status": "error",
            "error": f"unexpected: {type(e).__name__}: {e}",
            "workflow_run_id": run_id,
        }
    finally:
        await _runner_cleanup(engine)


async def _runner_cleanup(engine: WorkflowEngine) -> None:
    """Best-effort teardown: drain all touched handles, then close every
    spawned handle. Each step swallows its own errors so a hung drain
    or a flaky close can't strand the runner."""
    try:
        await engine.drain()
    except Exception as e:  # noqa: BLE001 — drain is best-effort
        engine.log(f"runner cleanup: drain raised {type(e).__name__}: {e}")
    for handle in list(engine._spawned_handles):
        try:
            await engine.close(handle)
        except Exception as e:  # noqa: BLE001 — close is best-effort
            engine.log(
                f"runner cleanup: close({handle!r}) raised "
                f"{type(e).__name__}: {e}")
