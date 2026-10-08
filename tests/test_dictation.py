"""The server's half of dictation: pinned files, a verified download, keywords."""

import asyncio
import hashlib
import http.server
import threading
from pathlib import Path

import pytest

from aegis import dictation
from aegis.dictation import Pin, Store, Unavailable, keywords, pin_id

FILES = {"needle.js": b"js", "needle.wasm": b"\0asm", "whistle.cact": b"model" * 1000}


@pytest.fixture
def origin():
    hits: list[str] = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            body = FILES.get(self.path.lstrip("/"))
            self.send_response(200 if body else 404)
            self.end_headers()
            if body:
                self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", hits
    srv.shutdown()


def pins(
    base: str, bad: str | None = None, missing: str | None = None
) -> tuple[Pin, ...]:
    return tuple(
        Pin(
            n,
            f"{base}/{'gone' if n == missing else n}",
            "0" * 64 if n == bad else hashlib.sha256(b).hexdigest(),
        )
        for n, b in FILES.items()
    )


def test_ensure_downloads_verifies_and_serves(tmp_path, origin):
    base, hits = origin
    s = Store(tmp_path, pins(base))
    asyncio.run(s.ensure())
    assert s.ready()
    assert (s.dir / "whistle.cact").read_bytes() == FILES["whistle.cact"]
    assert s.file(s.id, "needle.js") == s.dir / "needle.js"
    assert s.file(s.id, "other") is None
    assert s.file("0" * 12, "needle.js") is None
    asyncio.run(s.ensure())
    assert len(hits) == 3, "a ready directory downloads nothing"


def test_a_wrong_hash_fails_by_name_and_leaves_nothing(tmp_path, origin):
    s = Store(tmp_path, pins(origin[0], bad="needle.wasm"))
    with pytest.raises(Unavailable, match="needle.wasm"):
        asyncio.run(s.ensure())
    assert not (s.dir / "needle.wasm").exists()
    assert not list(s.dir.glob(".*.part"))
    assert not s.ready()
    assert s.file(s.id, "needle.wasm") is None


def test_a_failed_download_is_retried_on_the_next_call(tmp_path, origin):
    base, _ = origin
    s = Store(tmp_path, pins(base, missing="needle.js"))
    with pytest.raises(Unavailable, match="needle.js: HTTP 404"):
        asyncio.run(s.ensure())
    s.pins = pins(base)
    asyncio.run(s.ensure())
    assert s.ready()


def test_concurrent_calls_share_one_download(tmp_path, origin):
    base, hits = origin
    s = Store(tmp_path, pins(base))

    async def three():
        await asyncio.gather(s.ensure(), s.ensure(), s.ensure())

    asyncio.run(three())
    assert sorted(hits) == ["/needle.js", "/needle.wasm", "/whistle.cact"]


def test_an_unwritable_cache_is_unavailable_not_a_crash(tmp_path, origin):
    s = Store(tmp_path, pins(origin[0]))
    s.dir.parent.mkdir(parents=True, exist_ok=True)
    s.dir.write_text("a file where the directory goes")
    with pytest.raises(Unavailable, match="cache directory"):
        asyncio.run(s.ensure())


def test_pin_id_follows_the_hashes():
    assert pin_id() == pin_id(dictation.PINS)
    assert len(pin_id()) == 12
    assert pin_id(pins("http://x")) != pin_id()


def test_default_dir_reads_the_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_DICTATION_DIR", str(tmp_path))
    assert dictation.default_dir() == tmp_path
    monkeypatch.delenv("AEGIS_DICTATION_DIR")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "c"))
    assert dictation.default_dir() == tmp_path / "c" / "aegis" / "dictation"


def test_keywords_merge_dedupe_and_cap():
    kw = keywords(
        ["aegis-whistle-mic", None, "Pull-Request"],
        [Path("/w/repos/aegis"), Path("/w/scriptorium")],
        ["opus", "general"],
    )
    assert kw[: len(dictation.VOCABULARY)] == list(dictation.VOCABULARY)
    for w in ("aegis whistle mic", "scriptorium", "opus", "general"):
        assert w in kw
    lower = [k.lower() for k in kw]
    assert lower.count("aegis") == 1
    assert lower.count("pull request") == 1
    assert (
        len(keywords([f"h{i}" for i in range(500)], [], [])) == dictation.MAX_KEYWORDS
    )
