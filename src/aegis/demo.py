from aegis import AegisServer, WorkflowContext
from textwrap import dedent


server = AegisServer()


@server.prompt()
def init() -> str:
    """Initialize the connection and get a greeting."""
    return dedent("""
        Welcome to Aegis!
        """)


@server.workflow()
async def onboard(ctx: WorkflowContext):
    """Start the onboarding workflow."""

    await ctx.step(
        "You are onboarding into a new project. Your task is to explore the codebase and identify "
        "interesting files that can help you understand the project.\n\n"
        "Read a few key files to understand the project structure."
    )

    await ctx.step(
        "Now provide a comprehensive summary covering:\n"
        "1. What the project is (purpose)\n"
        "2. How it's organized (main directories, key files)\n"
        "3. How to run it\n"
        "4. Current development status.\n\n"
    )


def main():
    server.run(transport="http", host="127.0.0.1", port=4243)
