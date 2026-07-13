import asyncio, time, tempfile
from aegis.config import Agent, Lovelaice
from aegis.drivers.lovelaice import LovelaiceDriver

async def main():
    tmp = tempfile.mkdtemp()
    agent = Agent(provider=Lovelaice(model="anthropic/claude-haiku-4-5",
        base_url="https://openrouter.ai/api/v1",
        api_key_file="/home/apiad/Workspace/.claude/openrouter.token"))
    sess = LovelaiceDriver().session(agent, tmp, "", "i1")
    await sess.start()
    result = {}
    async def run():
        await sess.send("Write a detailed 800-word essay about the history of the printing press. Be thorough.")
        txt=""; res=None
        async for ev in sess.events():
            if type(ev).__name__=="AssistantText": txt+=getattr(ev,"text","")
            if type(ev).__name__=="Result": res=ev
        result["txt"]=txt; result["res"]=res
    task=asyncio.create_task(run())
    await asyncio.sleep(7.0)
    t0=time.monotonic()
    await sess.interrupt()
    await asyncio.wait_for(task, timeout=40)
    dt=time.monotonic()-t0
    await sess.close()
    words=len(result["txt"].split())
    print(f"words generated: {words} (interrupted mid-essay if << 800)")
    print(f"stopped {dt:.1f}s after interrupt")
    print("got terminal Result:", result["res"] is not None)

asyncio.run(main())
