"""Real-model probe: drive a native lovelaice agent through LovelaiceDriver
and confirm a real `read` tool call + answer. No FAKE_LLM — this exercises
the full path against a live model via OpenRouter.

Run: uv run python .playground/lovelaice-probe/probe.py
"""
import asyncio
import tempfile
from pathlib import Path

from aegis.config import Agent, Lovelaice
from aegis.drivers.lovelaice import LovelaiceDriver

TOKEN = "/home/apiad/Workspace/.claude/openrouter.token"


async def main():
    tmp = Path(tempfile.mkdtemp(prefix="lovel-probe-"))
    (tmp / "secret.txt").write_text("the magic number is 4217\n")

    agent = Agent(provider=Lovelaice(
        model="anthropic/claude-haiku-4-5",
        base_url="https://openrouter.ai/api/v1",
        api_key_file=TOKEN,
    ))
    driver = LovelaiceDriver()
    sess = driver.session(agent, str(tmp), "", "probe")
    await sess.start()
    await sess.send(
        "Read the file secret.txt in the current directory and tell me the magic number.")

    tool_calls, texts, kinds = [], [], []
    async for ev in sess.events():
        name = type(ev).__name__
        kinds.append(name)
        if name == "ToolUse":
            tool_calls.append((getattr(ev, "name", "?"), getattr(ev, "summary", "")))
        if name == "AssistantText":
            texts.append(getattr(ev, "text", ""))
    await sess.close()

    answer = "".join(texts)
    print("EVENT KINDS:", kinds)
    print("TOOL CALLS:", tool_calls)
    print("ANSWER:", answer)
    print("READ CALLED:", any("read" in (n or "").lower() for n, _ in tool_calls))
    print("ANSWER HAS 4217:", "4217" in answer)


if __name__ == "__main__":
    asyncio.run(main())
