import asyncio, tempfile
from pathlib import Path
from aegis.config import Agent, Lovelaice
from aegis.drivers.lovelaice import LovelaiceDriver

async def main():
    tmp = Path(tempfile.mkdtemp())
    agent = Agent(provider=Lovelaice(model="anthropic/claude-haiku-4-5",
        base_url="https://openrouter.ai/api/v1",
        api_key_file="/home/apiad/Workspace/.claude/openrouter.token"))
    sess = LovelaiceDriver().session(agent, str(tmp), "", "u")
    await sess.start(); await sess.send("Say hi in one word.")
    result = None
    async for ev in sess.events():
        if type(ev).__name__ == "Result": result = ev
    await sess.close()
    u = getattr(result, "usage", None)
    print("Result.usage:", u)
    print("input:", getattr(u,"input",None), "output:", getattr(u,"output",None))
    print("NONZERO:", bool(u and (getattr(u,"input",0) or getattr(u,"output",0))))

asyncio.run(main())
