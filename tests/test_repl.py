import asyncio
from rich.console import Console
from aegis.events import AssistantText, Result
from aegis.repl import run_repl


class FakeSession:
    def __init__(self):
        self.sent = []
        self.started = self.closed = False

    async def start(self):
        self.started = True

    async def send(self, text):
        self.sent.append(text)

    async def events(self):
        yield AssistantText(f"echo: {self.sent[-1]}")
        yield Result(duration_ms=10, is_error=False)

    async def close(self):
        self.closed = True


def test_repl_sends_inputs_and_renders_until_exit():
    sess = FakeSession()
    con = Console(record=True, width=80)
    inputs = iter(["hello", "world"])

    def fake_input(_prompt):
        try:
            return next(inputs)
        except StopIteration:
            raise EOFError

    asyncio.run(run_repl(sess, con, input_fn=fake_input))

    assert sess.started and sess.closed
    assert sess.sent == ["hello", "world"]
    out = con.export_text()
    assert "echo: hello" in out and "echo: world" in out


def test_repl_sends_initial_prompt_first():
    sess = FakeSession()
    con = Console(record=True, width=80)

    def fake_input(_p):
        raise EOFError

    asyncio.run(run_repl(sess, con, input_fn=fake_input,
                         initial_prompt="kickoff"))
    assert sess.sent == ["kickoff"]
