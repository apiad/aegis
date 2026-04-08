import functools
from textwrap import dedent
from fastmcp import FastMCP, Context
from pydantic import BaseModel
from pathlib import Path
from collections.abc import AsyncGenerator
from typing import Any, Callable, cast

from uuid import uuid4


class AegisServer(FastMCP):
    def __init__(self, name: str = "Aegis", *args, **kwargs):
        super().__init__(name, *args, **kwargs)
        self._workflows: dict[str, Callable] = {}
        self._runtimes: dict[str, AsyncGenerator] = {}

    def workflow(self, name: str | None = None):
        """Decorator to define a workflow as an async generator."""

        def decorator(func: Callable):
            workflow_name = name or func.__name__
            self._workflows[workflow_name] = func

            @self.prompt(name=func.__name__)
            @functools.wraps(func)
            async def wrapper(*args, **kwargs) -> Any:
                return f"Call tool `workflow_start(\"{workflow_name}\")` to start this workflow."

            return func


        return decorator


server = AegisServer()


@server.tool()
async def workflow_start(ctx: Context, name: str) -> str:
    """Start a workflow by name."""
    workflow_func = server._workflows.get(name)

    if not workflow_func:
        return f"No workflow found with name '{name}'."

    generator = workflow_func()
    uuid = str(uuid4())

    cast(AegisServer, ctx.fastmcp)._runtimes[uuid] = generator

    await ctx.set_state("active_workflow", uuid)

    instruction = await generator.__anext__()

    return instruction + "\n\n" + "Call tool `workflow_step()` when done."


@server.tool()
async def workflow_step(ctx: Context) -> str:
    """Continue to the next step of the active workflow."""
    uuid = await ctx.get_state("active_workflow")
    if not uuid:
        return "No active workflow."

    generator = cast(AegisServer, ctx.fastmcp)._runtimes.get(uuid)

    assert generator is not None, "Workflow generator not found."

    try:
        instruction = await generator.__anext__()
        return instruction + "\n\n" + "Call tool `workflow_step()` when done."
    except StopAsyncIteration:
        await ctx.delete_state("active_workflow")
        cast(AegisServer, ctx.fastmcp)._runtimes.pop(uuid, None)
        return "Workflow completed completed."


@server.prompt()
def init() -> str:
    """Initialize the connection and get a greeting."""
    return dedent("""
        Welcome to Aegis!
        """)


@server.workflow()
async def onboard():
    """Start the onboarding workflow."""

    yield (
        "You are onboarding into a new project. Your task is to explore the codebase and identify "
        "interesting files that can help you understand the project.\n\n"
        "Read a few key files to understand the project structure."
    )

    yield (
        "Now provide a comprehensive summary covering:\n"
        "1. What the project is (purpose)\n"
        "2. How it's organized (main directories, key files)\n"
        "3. How to run it\n"
        "4. Current development status.\n\n"
    )


def main():
    server.run(transport="http", host="127.0.0.1", port=4243)


if __name__ == "__main__":
    main()