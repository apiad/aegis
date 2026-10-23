"""The server's half of reading aloud with Phonon: pinned files in
subdirectories, the disk guard, and the route that serves them (#637)."""

import asyncio
import hashlib
import http.server
import threading

import pytest
from starlette.testclient import TestClient

from aegis import speech
from aegis.dictation import Pin, Unavailable, pin_id
from aegis.speech import SpeechStore

FILES = {
    "index.js": b"export {}",
    "wasm/phonon_tts_bg.wasm": b"\0asm",
    "model.q8.gguf": b"gguf" * 1000,
}


@pytest.fixture
def origin():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = FILES.get(self.path.lstrip("/"))
            self.send_response(200 if body else 404)
            self.end_headers()
            if body:
                self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def pins(base: str) -> tuple[Pin, ...]:
    return tuple(
        Pin(n, f"{base}/{n}", hashlib.sha256(b).hexdigest()) for n, b in FILES.items()
    )


def test_a_name_with_a_directory_downloads_beside_its_part_file(tmp_path, origin):
    s = SpeechStore(tmp_path, pins(origin), min_free_gb=0)
    asyncio.run(s.ensure())
    assert s.ready()
    assert (s.dir / "wasm" / "phonon_tts_bg.wasm").read_bytes() == b"\0asm"
    assert (
        s.file(s.id, "wasm/phonon_tts_bg.wasm") == s.dir / "wasm" / "phonon_tts_bg.wasm"
    )
    assert not list(s.dir.rglob(".*.part")), "no part file is left behind"


def test_a_name_outside_the_pins_is_not_served(tmp_path, origin):
    s = SpeechStore(tmp_path, pins(origin), min_free_gb=0)
    asyncio.run(s.ensure())
    assert s.file(s.id, "../speech.py") is None
    assert s.file(s.id, "wasm") is None
    assert s.file("0" * 12, "index.js") is None, "another pin's path"


def test_a_short_disk_refuses_to_start_the_download(tmp_path, origin):
    s = SpeechStore(tmp_path, pins(origin), min_free_gb=10**9)
    with pytest.raises(Unavailable, match="GB free"):
        asyncio.run(s.ensure())
    assert not s.dir.exists(), "nothing was written"


def test_files_already_there_need_no_room(tmp_path, origin):
    asyncio.run(SpeechStore(tmp_path, pins(origin), min_free_gb=0).ensure())
    asyncio.run(SpeechStore(tmp_path, pins(origin), min_free_gb=10**9).ensure())


def test_the_pins_cover_what_the_package_imports():
    """worker.js imports the single-threaded build only, off an isolated page;
    index.js its siblings. A missing one would 404 in the browser."""
    names = {p.name for p in speech.PINS}
    assert {
        "index.js",
        "worker.js",
        "fetch.js",
        "hub.js",
        "wav.js",
        "device.js",
        "simd.js",
        "threads.js",
    } <= names
    assert {
        "wasm/phonon_tts.js",
        "wasm/phonon_tts_bg.wasm",
        "wasm/phonon_tts_simd_bg.wasm",
    } <= names
    assert {
        "config.json",
        "tokenizer.json",
        "model.q8.gguf",
        "default-voice.safetensors",
    } <= names
    assert not any(n.startswith("wasm-threads/") for n in names)
    assert all(len(p.sha256) == 64 for p in speech.PINS)
    assert len({pin_id(speech.PINS)}) == 1


def test_default_dir_reads_the_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_SPEECH_DIR", str(tmp_path))
    assert speech.default_dir() == tmp_path
    monkeypatch.delenv("AEGIS_SPEECH_DIR")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "c"))
    assert speech.default_dir() == tmp_path / "c" / "aegis" / "speech"


def test_the_route_serves_a_pinned_file_forever_and_nothing_else(tmp_path, monkeypatch):
    from aegis.web import build_web

    from .test_attachment_ops import make_app

    monkeypatch.setenv("AEGIS_SPEECH_DIR", str(tmp_path / "speech"))
    app = make_app(tmp_path, "claude")
    d = app.speech.dir
    for p in app.speech.pins:
        (d / p.name).parent.mkdir(parents=True, exist_ok=True)
        (d / p.name).write_bytes(b"\0asm" if p.name.endswith(".wasm") else b"x")
    client = TestClient(build_web(app, "tok", {"testserver"}))
    r = client.get(f"/speech/{app.speech.id}/wasm/phonon_tts_bg.wasm")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/wasm", (
        "instantiateStreaming needs it"
    )
    assert "immutable" in r.headers["cache-control"]
    assert client.get(f"/speech/{app.speech.id}/wasm/nope.wasm").status_code == 404
    assert client.get(f"/speech/{app.speech.id}/../../.aegis.yaml").status_code == 404
