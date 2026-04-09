import json
import logging
import os
from textwrap import dedent
from uuid import uuid4
from fastmcp import FastMCP, Context
from typing import Any, Callable, Coroutine, Generic, TypeVar
from pydantic import BaseModel, TypeAdapter, ValidationError
from asyncio import Queue, create_task, sleep
from asyncio.tasks import Task


class MaxRetriesExceededError(Exception):
    """Raised when workflow step exceeds maximum retry attempts."""

    pass


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

    async def __aenter__(self) -> "Attempt[T]":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_type is not None and not any(
            issubclass(exc_type, e) for e in self.on_errors
        ):
            return False
        if self._attempt >= self.max_attempts:
            return False
        return True

    def __aiter__(self) -> "Attempt[T]":
        return self

    async def __anext__(self) -> T:
        if self._attempt >= self.max_attempts:
            raise StopAsyncIteration

        try:
            if self._data is None:
                self._data = await self.ctx.step(self.instruction, self.response_type)
            if self._data is None:
                raise StopAsyncIteration
            return self._data

        except self.on_errors as e:
            self._attempt += 1
            if self._attempt >= self.max_attempts:
                raise StopAsyncIteration

            error_instruction = (
                f"[ERROR] {e}\n\n"
                f"You have {self.max_attempts - self._attempt} retries remaining.\n\n"
                f"{self.instruction}\n\n"
                "Fix the issue and provide new values."
            )
            self._data = await self.ctx.step(error_instruction, self.response_type)
            self.ctx.reset_retry()
            return await self.__anext__()


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
        self._last_instruction: str = ""
        self._last_response_type: type | None = None

    def reset_retry(self) -> None:
        self.retry_count = 0

    def attempt[T: BaseModel](
        self,
        instruction: str,
        response_type: type[T],
        on_errors: tuple[type[Exception], ...] = (Exception,),
        max_attempts: int = 3,
    ) -> Attempt[T]:
        """Create an attempt context for retry on specific exceptions."""
        return Attempt(self, instruction, response_type, on_errors, max_attempts)

    async def step[T: BaseModel](
        self, instruction: str, response_type: type[T] | None = None
    ) -> T | None:
        """Yield an instruction to the user and wait for them to call `workflow_step()`."""
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

        while True:
            self.out_queue.put_nowait(base_instruction)
            logger.debug("[step] Waiting for response from in_queue")
            response = await self.in_queue.get()
            logger.debug(f"[step] Got response: {response}")

            error_msg: str | None = None
            parsed_response: Any = None

            if response_type is None:
                if response is not None:
                    error_msg = "Expected no data (step expects no response), but received data. Please call `workflow_step()` with no arguments."
            else:
                if response is None:
                    error_msg = "Expected JSON data matching schema, but received nothing. Provide valid JSON."
                else:
                    try:
                        parsed_response = TypeAdapter(response_type).validate_json(
                            response
                        )
                    except ValidationError as e:
                        error_msg = f"Validation failed: {e}"

            if error_msg:
                self.retry_count += 1
                if self.retry_count >= self.max_retries:
                    raise MaxRetriesExceededError(
                        f"Step failed after {self.retry_count} retries: {error_msg}"
                    )
                retries_left = self.max_retries - self.retry_count
                full_instruction = (
                    f"[ERROR] {error_msg}\n\n"
                    f"[RETRY] You have {retries_left} retries remaining.\n\n"
                    f"[INSTRUCTION]\n{base_instruction}\n\n"
                    f"Call workflow_step(...) again with correct data."
                )
                self.out_queue.put_nowait(full_instruction)
                response = await self.in_queue.get()
                continue

            self.reset_retry()
            return parsed_response


class AegisServer(FastMCP):
    def __init__(self, name: str = "Aegis", *args, **kwargs):
        super().__init__(name, *args, **kwargs)
        self._workflows: dict[str, tuple[Callable, int]] = {}
        self._tasks: dict[str, Task] = {}
        self._contexts: dict[str, WorkflowContext] = {}

        @self.tool()
        async def workflow_start(ctx: Context, name: str) -> str:
            """Start a workflow by name."""
            logger.debug(f"[workflow_start] Starting workflow: {name}")
            workflow_data = self._workflows.get(name)

            if not workflow_data:
                return f"No workflow found with name '{name}'."

            workflow_func, max_retries = workflow_data

            uuid = str(uuid4())
            logger.debug(f"[workflow_start] Created uuid: {uuid}")
            context = WorkflowContext(cwd=os.getcwd(), max_retries=max_retries)
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

            task.add_done_callback(lambda t, u=uuid, ctx=ctx: self._cleanup(u, ctx))

            await ctx.set_state("active_workflow", uuid)

            logger.debug("[workflow_start] Waiting for instruction from out_queue")
            instruction = await context.out_queue.get()
            logger.debug(
                f"[workflow_start] Got instruction: {instruction[:50] if instruction else None}..."
            )

            return instruction + "\n\nCall tool `workflow_step()` when done."

        @self.tool()
        async def workflow_step(ctx: Context, data: Any = None) -> str:
            """Continue to the next step of the active workflow."""
            logger.debug(f"[workflow_step] Called with response: {data}")
            uuid = await ctx.get_state("active_workflow")
            if not uuid:
                return "No active workflow."

            context = self._contexts.get(uuid)
            assert context is not None, "Workflow context not found."

            if data is not None:
                if isinstance(data, str):
                    logger.debug("[workflow_step] Putting string in in_queue")
                    context.in_queue.put_nowait(data)
                else:
                    logger.debug("[workflow_step] Putting JSON in in_queue")
                    context.in_queue.put_nowait(json.dumps(data))
            else:
                logger.debug("[workflow_step] Putting None in in_queue")
                context.in_queue.put_nowait(None)

            await sleep(0)  # Yield to let the task run

            task = self._tasks.get(uuid)
            logger.debug(
                f"[workflow_step] Task done: {task.done() if task else 'no task'}"
            )

            if task and task.done():
                if task.exception() is not None:
                    logger.debug("[workflow_step] Task failed with exception")
                    return "Workflow ended with error. See above for details."

                logger.debug("[workflow_step] Task is done, returning completed")
                return "Workflow completed."

            logger.debug("[workflow_step] Waiting for instruction from out_queue")
            instruction = await context.out_queue.get()
            logger.debug(
                f"[workflow_step] Got instruction: {instruction[:50] if instruction else None}..."
            )

            return instruction

    async def _cleanup(self, uuid: str, ctx: Context) -> None:
        await ctx.delete_state("active_workflow")
        self._tasks.pop(uuid, None)
        self._contexts.pop(uuid, None)

    def workflow(self, name: str | None = None, max_retries: int = 3):
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

            return wrapper

        return decorator
