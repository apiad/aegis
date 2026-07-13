import asyncio, tempfile
from pathlib import Path
from aegis.config import Agent, Lovelaice
from aegis.drivers.lovelaice import LovelaiceDriver

def mk():
    return Agent(provider=Lovelaice(model="anthropic/claude-haiku-4-5",
        base_url="https://openrouter.ai/api/v1",
        api_key_file="/home/apiad/Workspace/.claude/openrouter.token"))

async def main():
    tmp = Path(tempfile.mkdtemp())
    drv = LovelaiceDriver()

    # --- streaming ---
    s = drv.session(mk(), str(tmp), "", "s1"); await s.start()
    await s.send("Write a short two-sentence paragraph about the sea.")
    n_text = 0
    async for ev in s.events():
        if type(ev).__name__ == "AssistantText": n_text += 1
    sid = s.session_id
    await s.close()
    print("STREAM: AssistantText events =", n_text, "(>1 means streaming)")

    # --- resume (fresh subprocess) ---
    s1 = drv.session(mk(), str(tmp), "", "r1"); await s1.start()
    await s1.send("Remember the codeword is BANANA. Just acknowledge briefly.")
    async for _ in s1.events(): pass
    rsid = s1.session_id
    await s1.close()
    s2 = drv.resume(mk(), str(tmp), "", "r2", session_id=rsid); await s2.start()
    await s2.send("What is the codeword I told you? Reply with just the word.")
    txt = ""
    async for ev in s2.events():
        if type(ev).__name__ == "AssistantText": txt += getattr(ev,"text","")
    await s2.close()
    print("RESUME: reply =", repr(txt.strip()[:80]), "| BANANA recalled:", "BANANA" in txt.upper())

asyncio.run(main())
