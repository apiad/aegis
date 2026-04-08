import functools
import json
from uuid import uuid4
from fastmcp import FastMCP, Context
from typing import Any, Callable, Coroutine
from pydantic import BaseModel, TypeAdapter
from asyncio import Queue, create_task
from asyncio.tasks import Task


class WorkflowContext:
    """Context for workflow execution, allowing state management across steps."""

    def __init__(self) -> None:
        self.in_queue: Queue[Any] = Queue()
        self.out_queue: Queue[Any] = Queue()
        self._task: Task | None = None
        self._result: Any = None

    async def step[T: BaseModel](
        self, instruction: str, response_type: type[T] | None = None
    ) -> T | None:
        """Yield an instruction to the user and wait for them to call `workflow_step()`."""
        full_instruction = instruction

        if response_type is not None:
            schema = response_type.model_json_schema()
            schema_str = json.dumps(schema, indent=2)
            full_instruction += (
                f"\n\nProvide your response as a JSON-stringified object matching this schema:\n"
                f"```json\n{schema_str}\n```\n"
                f"Then call tool `workflow_step(response='<your JSON string>')`."
            )
        else:
            full_instruction += (
                f"\n\nCall tool `workflow_step()` without arguments when done."
            )

        self.out_queue.put_nowait(full_instruction)
        response = await self.in_queue.get()

        if response_type is not None and response is not None:
            return TypeAdapter(response_type).validate_json(response)

        return response


class AegisServer(FastMCP):
    def __init__(self, name: str = "Aegis", *args, **kwargs):
        super().__init__(name, *args, **kwargs)
        self._workflows: dict[str, Callable] = {}
        self._tasks: dict[str, Task] = {}
        self._contexts: dict[str, WorkflowContext] = {}

        @self.tool()
        async def workflow_start(ctx: Context, name: str) -> str:
            """Start a workflow by name."""
            workflow_func = self._workflows.get(name)

            if not workflow_func:
                return f"No workflow found with name '{name}'."

            uuid = str(uuid4())
            context = WorkflowContext()
            self._contexts[uuid] = context
            coroutine = workflow_func(context)
            task = create_task(coroutine)
            self._tasks[uuid] = task
            context._task = task

            task.add_done_callback(lambda t, u=uuid, ctx=ctx: self._cleanup(u, ctx))

            await ctx.set_state("active_workflow", uuid)

            instruction = await context.out_queue.get()

            return instruction + "\n\nCall tool `workflow_step()` when done."

        @self.tool()
        async def workflow_step(ctx: Context, response: Any = None) -> str:
            """Continue to the next step of the active workflow."""
            uuid = await ctx.get_state("active_workflow")
            if not uuid:
                return "No active workflow."

            context = self._contexts.get(uuid)
            assert context is not None, "Workflow context not found."

            if response is not None:
                if isinstance(response, str):
                    context.in_queue.put_nowait(response)
                else:
                    context.in_queue.put_nowait(json.dumps(response))
            else:
                context.in_queue.put_nowait(None)

            task = self._tasks.get(uuid)
            if task and task.done():
                return "Workflow completed."

            return await context.out_queue.get()

    async def _cleanup(self, uuid: str, ctx: Context) -> None:
        await ctx.delete_state("active_workflow")
        self._tasks.pop(uuid, None)
        self._contexts.pop(uuid, None)

    def workflow(self, name: str | None = None):
        """
        Decorator to define a workflow as an async generator.

        Usage:

        ```python
        @server.workflow(name="...") # optional name, defaults to function name
        async def my_workflo(ctx: WorkflowContext):
            "Workflow description here."
            # Run arbitrary async code
            await ctx.step()  # yield instructions and wait LLM
        ```

        Read `ContextWorkflow` documentation for details on how to yield instructions and receive responses from the user.
        """

        def decorator(func: Callable[[WorkflowContext], Coroutine]):
            workflow_name = name or func.__name__
            self._workflows[workflow_name] = func

            @self.prompt(name=func.__name__)
            async def wrapper() -> Any:
                return f'We are preparing to run workflow `{workflow_name}`.\nCall Aegis tool `workflow_start(name="{workflow_name}")` to start this workflow.'

            return wrapper

        return decorator
