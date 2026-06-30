from __future__ import annotations

import asyncio
from pathlib import Path

from aegis.config import WebConfig
from aegis.tui.file_index import FileIndexer
from aegis.web.subscriptions import SubscriptionRegistry
from aegis.web.wssession import WSSession, WSDisconnect

CONSTANTS = {"RESUME_GAP_CAP": 1000}
_DISCO = object()


def _setup(tmp: Path) -> SubscriptionRegistry:
    (tmp / "a.md").write_text("# Title\n**bold**", encoding="utf-8")
    (tmp / "sub").mkdir()
    (tmp / "sub" / "b.html").write_text("<h1>Hi</h1>", encoding="utf-8")
    (tmp / "c.py").write_text("print('x')", encoding="utf-8")
    idx = FileIndexer()
    idx._cwd = tmp.resolve()
    idx._paths = ["a.md", "sub/b.html", "c.py"]
    reg = SubscriptionRegistry(object(), tmp / "state")
    reg.set_files(idx, tmp.resolve())
    return reg


def test_file_search(tmp_path: Path):
    reg = _setup(tmp_path)
    assert "a.md" in reg.file_search("")
    assert reg.file_search("html") == ["sub/b.html"]


def test_file_read_markdown(tmp_path: Path):
    r = _setup(tmp_path).file_read("a.md")
    assert r["kind"] == "markdown" and "# Title" in r["content"]


def test_file_read_html(tmp_path: Path):
    r = _setup(tmp_path).file_read("sub/b.html")
    assert r["kind"] == "html" and "<h1>Hi</h1>" in r["content"]


def test_file_read_source(tmp_path: Path):
    r = _setup(tmp_path).file_read("c.py")
    assert r["kind"] == "source" and "print" in r["content"]


def test_file_read_traversal_blocked(tmp_path: Path):
    (tmp_path.parent / "secret.txt").write_text("nope", encoding="utf-8")
    r = _setup(tmp_path).file_read("../secret.txt")
    assert "error" in r


def test_file_read_missing(tmp_path: Path):
    r = _setup(tmp_path).file_read("nope.txt")
    assert "error" in r


def test_no_files_configured(tmp_path: Path):
    reg = SubscriptionRegistry(object(), tmp_path / "state")
    assert reg.file_search("x") == []
    assert "error" in reg.file_read("a.md")


# ---- WSSession integration ----

class FakeTransport:
    def __init__(self):
        self._in: asyncio.Queue = asyncio.Queue()
        self.sent: list[dict] = []
        self.closed = None

    def feed(self, f):
        self._in.put_nowait(f)

    def disconnect(self):
        self._in.put_nowait(_DISCO)

    async def receive_json(self):
        f = await self._in.get()
        if f is _DISCO:
            raise WSDisconnect()
        return f

    async def send_json(self, obj):
        self.sent.append(obj)

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)


class FakeSM:
    def list_agents(self): return []
    def list_sessions(self): return []
    def get(self, h): return None


async def _settle(n=4):
    for _ in range(n):
        await asyncio.sleep(0.01)


async def test_wssession_file_rpcs(tmp_path: Path):
    reg = _setup(tmp_path)
    t = FakeTransport()
    sess = WSSession(t, FakeSM(), reg, WebConfig(token="s"), CONSTANTS)
    task = asyncio.create_task(sess.run())
    t.feed({"type": "auth", "token": "s"})
    await _settle()
    t.feed({"type": "rpc", "id": 1, "method": "file_search",
            "params": {"query": "md"}})
    t.feed({"type": "rpc", "id": 2, "method": "file_read",
            "params": {"path": "a.md"}})
    await _settle()
    by_id = {s["id"]: s for s in t.sent if s.get("type") == "rpc_response"}
    assert by_id[1]["result"]["paths"] == ["a.md"]
    assert by_id[2]["result"]["kind"] == "markdown"
    t.disconnect()
    await asyncio.wait_for(task, timeout=1.0)
