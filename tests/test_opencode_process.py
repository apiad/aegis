import asyncio
import json
from pathlib import Path

import pytest

from aegis.harness import Launch
from aegis.opencode.process import OpenCodeProcess, probe

from .conftest import until


class Rig:
    def __init__(self, tmp_path: Path, bin: str, **kw):
        self.lines: list[dict] = []
        self.exits: list[tuple[int, list[str]]] = []
        self.errors: list[tuple[str, bool, str | None]] = []
        defaults = dict(
            cwd=tmp_path,
            model="opencode-go/fake-pro",
            effort="high",
            permission="full",
            resume_id=None,
            mcp=None,
            system_prompt="You are in aegis.",
            stderr_path=tmp_path / "stderr.log",
            on_line=lambda line: self.lines.append(json.loads(line)),
            on_exit=lambda code, tail: self.exits.append((code, tail)),
            on_error=lambda text, idle, line: self.errors.append((text, idle, line)),
        )
        self.launch = Launch(**(defaults | kw))
        self.proc = OpenCodeProcess(bin, self.launch)

    def types(self) -> list[str]:
        return [e["type"] for e in self.lines]

    def texts(self) -> list[str]:
        return [
            e["properties"]["part"]["text"]
            for e in self.lines
            if e["type"] == "message.part.updated"
            and e["properties"]["part"]["type"] == "text"
            and e["properties"]["part"]["text"]
        ]

    def idles(self) -> int:
        return self.types().count("session.idle")

    async def turn(self, text: str, n: int = 1) -> None:
        before = self.idles()
        await self.proc.send(text)
        await until(
            lambda: self.idles() >= before + n, timeout=5, what=f"{text!r} to end"
        )


@pytest.fixture
async def rig(tmp_path, fake_opencode):
    r = Rig(tmp_path, fake_opencode)
    await r.proc.start()
    yield r
    await r.proc.terminate()


async def test_start_makes_a_session_and_only_its_kept_events_arrive(rig):
    assert rig.proc.session_id.startswith("ses_") and rig.proc.running
    await rig.turn("hello")
    assert "you said: hello" in rig.texts()
    assert "message.part.delta" in rig.types()
    assert not {
        "plugin.added",
        "session.diff",
        "server.heartbeat",
        "server.connected",
    } & set(rig.types())


async def test_the_prompt_carries_model_variant_and_system(rig):
    await rig.turn("hello")
    await rig.turn("/body")
    body = json.loads(rig.texts()[-1].removeprefix("body: "))
    assert body["model"] == {"providerID": "opencode-go", "modelID": "fake-pro"}
    assert (body["variant"], body["system"]) == ("high", "You are in aegis.")


async def test_an_effort_the_model_does_not_list_is_not_sent(rig):
    await rig.proc.set("model", "opencode-go/fake-plain")
    await rig.turn("hello")
    await rig.turn("/body")
    body = json.loads(rig.texts()[-1].removeprefix("body: "))
    assert body["model"]["modelID"] == "fake-plain" and "variant" not in body


async def test_a_resume_keeps_the_id_and_an_unknown_one_starts_anew(
    tmp_path, fake_opencode
):
    first = Rig(tmp_path, fake_opencode)
    await first.proc.start()
    await first.turn("remember me")
    sid = first.proc.session_id
    await first.proc.terminate()
    again = Rig(tmp_path, fake_opencode, resume_id=sid)
    await again.proc.start()
    try:
        assert again.proc.session_id == sid
        await again.turn("/recall")
        assert "remember me" in again.texts()[-1]
    finally:
        await again.proc.terminate()
    lost = Rig(tmp_path, fake_opencode, resume_id="ses_gone")
    await lost.proc.start()
    try:
        assert lost.proc.session_id not in (None, "ses_gone")
    finally:
        await lost.proc.terminate()


async def test_a_permission_change_restarts_the_child_at_the_next_send_only(rig):
    pid = rig.proc.pid
    await rig.proc.send("/sleep 1")
    await rig.proc.set("permission", "read")
    await rig.proc.send("steer this")  # mid-turn: no restart
    assert rig.proc.pid == pid
    await until(lambda: rig.idles() >= 1, timeout=5, what="the turn")
    await rig.turn("/config")
    assert rig.proc.pid != pid
    rules = json.loads(rig.texts()[-1].removeprefix("config: "))["permission"]
    assert rules["edit"] == "deny" and rules["bash"] == "deny"


async def test_interrupt_aborts_the_turn(rig):
    await rig.proc.send("/sleep 5")
    await until(lambda: "message.part.updated" in rig.types(), what="the call")
    await asyncio.sleep(0.2)
    await rig.proc.interrupt()
    await until(lambda: "session.error" in rig.types(), what="the abort")


async def test_a_command_runs_without_blocking_and_an_unknown_one_is_an_error(rig):
    await rig.proc.send("/hello the okapi")  # returns before the turn ends
    await until(
        lambda: "you said: Say hello to the okapi." in rig.texts(), what="the command"
    )
    await until(lambda: rig.idles() >= 1, what="its end")
    await rig.proc.send("/nope x")
    await until(lambda: rig.errors, what="the refusal")
    text, idle, line = rig.errors[0]
    assert "400" in text and idle and line == "/nope x"


async def test_an_exit_reports_the_code_and_the_stderr_tail(rig):
    await rig.proc.send("/exit 3")
    await until(lambda: rig.exits, what="the exit")
    code, tail = rig.exits[0]
    assert code == 3 and any("exiting on request" in t for t in tail)
    assert not rig.proc.running


async def test_a_missing_binary_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        await Rig(tmp_path, str(tmp_path / "no-opencode")).proc.start()


async def test_a_child_that_dies_before_listening_raises(
    tmp_path, fake_opencode, monkeypatch
):
    monkeypatch.setenv("FAKE_OPENCODE_DIE", "1")
    with pytest.raises(OSError, match="dying before listening"):
        await Rig(tmp_path, fake_opencode).proc.start()


async def test_the_catalog_and_a_probe(rig, tmp_path, fake_opencode):
    cat = await rig.proc.catalog()
    assert cat.has("hello") and cat.model("opencode-go/fake-pro").window == 1000000
    probed = await probe(fake_opencode, tmp_path, tmp_path / "probe.log")
    assert [m.value for m in probed.models] == [m.value for m in cat.models]


async def test_a_start_that_fails_after_listening_ends_the_child(
    tmp_path, fake_opencode, monkeypatch
):
    log = tmp_path / "fake.log"
    monkeypatch.setenv("FAKE_OPENCODE_LOG", str(log))
    monkeypatch.setenv("FAKE_OPENCODE_FAIL_SESSION", "1")
    r = Rig(tmp_path, fake_opencode)
    with pytest.raises(Exception):
        await r.proc.start()
    (pid,) = [
        int(x.split()[1])
        for x in log.read_text().splitlines()
        if x.startswith("START ")
    ]
    await until(
        lambda: (
            not Path(f"/proc/{pid}").exists()
            or "State:\tZ" in Path(f"/proc/{pid}/status").read_text()
        ),
        what="the child gone",
    )
    assert not r.proc.running
