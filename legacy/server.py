import json
import logging
import os
from asyncio import Queue, create_task, sleep
from asyncio.tasks import Task
from collections.abc import Callable, Coroutine
from textwrap import dedent
from typing import Any, Generic, TypeVar, overload
from uuid import uuid4

from fastmcp import Context, FastMCP
from pydantic import BaseModel, TypeAdapter, ValidationError


T = TypeVar("T")


class Attempt(Generic[T]):
    """Async context manager + async iterator for retry on specific exceptions."""

    def __init__(
        self,
        ctx: "WorkflowContext",
        instruction: str,
        response_type: type[T],
        on_errors: tuple[type[Exception], ...],
        max_attempts: int,
    ):
        self.ctx = ctx
        self.instruction = instruction
        self.response_type = response_type
        self.on_errors = on_errors
        self.max_attempts = max_attempts
        self._attempt = 0
        self._data: T | None = None
        self._last_error: Exception | None = None

    async def __aenter__(self) -> "Attempt[T]":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_type is None:
            return False

        if not any(issubclass(exc_type, e) for e in self.on_errors):
            return False

        if self._attempt >= self.max_attempts:
            return False

        # Store the error so __anext__ can handle it
        self._last_error = exc_val
        return True

    def __aiter__(self) -> "Attempt[T]":
        return self

    async def __anext__(self) -> T:
        while self._attempt < self.max_attempts:
            try:
                # If we have an error from the previous execution of the block (__aexit__),
                # raise it now so we can catch it in the retry logic.
                if self._last_error is not None:
                    e = self._last_error
                    self._last_error = None
                    raise e

                # Call step() to get data. This will raise if validation fails.
                self._data = await self.ctx.step(self.instruction, self.response_type)

                # Step can only return None if response_type is None,
                # but Attempt expects a response_type.
                if self._data is None:
                    raise StopAsyncIteration

                return self._data

            except self.on_errors as e:
                self._attempt += 1
                if self._attempt >= self.max_attempts:
                    break

                self.instruction = (
                    f"[ERROR] {e}\n\n"
                    f"You have {self.max_attempts - self._attempt} retries remaining.\n\n"
                    f"{self.instruction}"
                )
                self._data = None
                self.ctx.out_queue.put_nowait(self.instruction)

        raise StopAsyncIteration


logger = logging.getLogger("aegis")


class WorkflowContext:
    """Context for workflow execution, allowing state management across steps."""

    def __init__(self, cwd: str = ".", max_retries: int = 3) -> None:
        self.in_queue: Queue[Any] = Queue()
        self.out_queue: Queue[Any] = Queue()
        self._task: Task | None = None
        self._result: Any = None
        self.cwd = cwd
        self.retry_count: int = 0
        self.max_retries: int = max_retries

    def reset_retry(self) -> None:
        self.retry_count = 0

    def attempt[T: BaseModel](
        self,
        instruction: str,
        response_type: type[T],
        on_errors: tuple[type[Exception], ...] = (Exception,),
        max_attempts: int = 3,
    ) -> Attempt[T]:
        """Create an Attempt context manager + iterator."""
        return Attempt(self, instruction, response_type, on_errors, max_attempts)

    @overload
    async def step[T: BaseModel](self, instruction: str, response_type: type[T]) -> T:
        ...

    @overload
    async def step(self, instruction: str) -> None:
        ...

    async def step[T: BaseModel](
        self, instruction: str, response_type: type[T] | None = None
    ) -> T | None:
        """Yield an instruction to the user and wait for them to call `workflow_step()`."""
        # Clear in_queue to avoid stale responses
        while not self.in_queue.empty():
            self.in_queue.get_nowait()

        base_instruction = dedent(instruction)

        if response_type is not None:
            schema = response_type.model_json_schema()
            schema_str = json.dumps(schema, indent=2)
            base_instruction += (
                f"\n\nProvide your response as a JSON-stringified object matching this schema:\n"
                f"```json\n{schema_str}\n```\n"
                f"Then call tool `workflow_step(response='<your JSON string>')`."
            )
        else:
            base_instruction += "\n\nCall tool `workflow_step()` with NO arguments when done. No arguments needed in this step."

        self.out_queue.put_nowait(base_instruction)
        logger.debug("[step] Waiting for response from in_queue")
        response = await self.in_queue.get()
        logger.debug(f"[step] Got response: {response}")

        if response_type is None:
            if response is not None:
                error_msg = "Expected no data (step expects no response), but received data. Please call `workflow_step()` with no arguments."
                self.out_queue.put_nowait(f"[ERROR] {error_msg}\n\n{base_instruction}")
                raise ValueError(error_msg)
            return None

        if response is None:
            error_msg = "Expected JSON data matching schema, but received nothing. Provide valid JSON."
            self.out_queue.put_nowait(f"[ERROR] {error_msg}\n\n{base_instruction}")
            raise ValueError(error_msg)

        try:
            return TypeAdapter(response_type).validate_json(response)
        except ValidationError as e:
            error_msg = f"Validation failed: {e}"
            self.out_queue.put_nowait(f"[ERROR] {error_msg}\n\n{base_instruction}")
            raise ValueError(error_msg)


class AegisServer(FastMCP):
    def __init__(self, name: str = "Aegis", *args, **kwargs):
        super().__init__(name, *args, **kwargs)
        self._workflows: dict[str, tuple[Callable, int]] = {}
        self._tasks: dict[str, Task] = {}
        self._contexts: dict[str, WorkflowContext] = {}

        @self.tool()
        async def workflow_start(
            ctx: Context, name: str, cwd: str | None = None
        ) -> str:
            """Start a workflow by name."""
            logger.debug(f"[workflow_start] Starting workflow: {name}")
            workflow_data = self._workflows.get(name)

            if not workflow_data:
                return f"No workflow found with name '{name}'."

            workflow_func, max_retries = workflow_data

            uuid = str(uuid4())
            logger.debug(f"[workflow_start] Created uuid: {uuid}")
            context = WorkflowContext(cwd=cwd or os.getcwd(), max_retries=max_retries)
            self._contexts[uuid] = context

            async def wrapped_workflow():
                try:
                    await workflow_func(context)
                except Exception as e:
                    error_msg = (
                        f"[ERROR] Workflow ended with error: {e}\n\n"
                        "The workflow has failed. Contact the developer if this persists."
                    )
                    context.out_queue.put_nowait(error_msg)
                    logger.debug(f"[workflow] Uncaught exception: {e}")

            coroutine = wrapped_workflow()
            task = create_task(coroutine)
            self._tasks[uuid] = task
            context._task = task

            task.add_done_callback(
                lambda t, u=uuid, ctx=ctx: create_task(self._cleanup(u, ctx))
            )

            await ctx.set_state("active_workflow", uuid)

            logger.debug("[workflow_start] Waiting for instruction from out_queue")
            instruction = await context.out_queue.get()
            logger.debug(
                f"[workflow_start] Got instruction: {instruction[:50] if instruction else None}..."
            )

            return f"Workflow started [ID: {uuid}]\n\n{instruction}\n\nCall tool `workflow_step()` when done."

        @self.tool()
        async def workflow_step(ctx: Context, data: Any = None) -> str:
            """Continue to the next step of the active workflow."""
            logger.debug(f"[workflow_step] Called with response: {data}")
            uuid = await ctx.get_state("active_workflow")
            if not uuid:
                return "No active workflow."

            context = self._contexts.get(uuid)
            if not context:
                return "Workflow context not found."

            if data is not None:
                if isinstance(data, str):
                    context.in_queue.put_nowait(data)
                else:
                    context.in_queue.put_nowait(json.dumps(data))
            else:
                context.in_queue.put_nowait(None)

            await sleep(0)  # Yield to let the task run

            task = self._tasks.get(uuid)

            # Prioritize queue
            if not context.out_queue.empty():
                instruction = await context.out_queue.get()
                return instruction

            if task and task.done():
                if task.exception() is not None:
                    return "Workflow ended with error. See above for details."
                return "Workflow completed."

            instruction = await context.out_queue.get()
            return instruction

    async def _cleanup(self, uuid: str, ctx: Context) -> None:
        await ctx.delete_state("active_workflow")
        self._tasks.pop(uuid, None)
        self._contexts.pop(uuid, None)

    def workflow(self, name: str | None = None, max_retries: int = 3):
        """
        Decorator to define a workflow as an async function.

        ```python
        @server.workflow()
        async def my_worflow(ctx: WorkflowContext):
            await ctx.step("First instruction")
            # ...
        ```
        """

        def decorator(
            func: Callable[[WorkflowContext], Coroutine[Any, Any, Any]],
        ) -> Any:
            func_name: str | None = getattr(func, "__name__", None)
            if name is not None:
                workflow_name = name
            elif func_name is not None:
                workflow_name = func_name
            else:
                raise ValueError(
                    "Workflow function must have a name or provide one via the 'name' parameter"
                )
            self._workflows[workflow_name] = (func, max_retries)

            description: str | None = func.__doc__ if func.__doc__ is not None else None

            @self.prompt(name=workflow_name, description=description)
            async def wrapper() -> Any:
                return f'We are preparing to run workflow `{workflow_name}`.\nCall Aegis tool `workflow_start(name="{workflow_name}")` to start this workflow.'

            return func

        return decorator
