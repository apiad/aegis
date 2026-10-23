"""The server's half of reading replies aloud with Phonon: the pinned files.

Speech is made in the browser (DESIGN.md, "Audio never leaves the browser"),
by the browser's own voice unless the person opts in to Phonon on that device.
For Phonon the server keeps the ``phonon-tts`` package and the 31M English
model on disk, verified against the hashes below, and serves them at
``/speech/<pin>/<name>`` with an immutable cache header (web.py), the way
dictation.py keeps Whistle. Same origin, so the browser's Cache API keeps the
65 MB of the model across reloads, and an outage of the CDN or the Hub does
not take away a voice already chosen.

Only the 31M English model is pinned: the 40M models, the multilingual one
included, are gated on Hugging Face and licensed CC BY-NC 4.0
(docs/superpowers/specs/2026-10-23-read-aloud-design.html). The threaded wasm
build is left out: it needs a cross-origin isolated page, which aegis's is not.

Nothing is downloaded until a browser opts in, and not on a disk with less than
MIN_FREE_GB free: the files weigh 74 MB, and a full disk stops every session.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .dictation import Pin, Store, Unavailable

_PKG = "https://cdn.jsdelivr.net/npm/phonon-tts@0.4.1"
_MODEL = "https://huggingface.co/gradium/phonon-31m-en/resolve/92741c5b07d085ff840e26fea8ce43110b87852f"
PINS = (
    Pin(
        "index.js",
        f"{_PKG}/index.js",
        "46236d512812767e46a82685f95820e3e341373c6c52d8ab3e861badcbeb8a43",
    ),
    Pin(
        "fetch.js",
        f"{_PKG}/fetch.js",
        "fe28ae73b476a1b014098843335141efad3a943c98939a72d2d907ee4596582c",
    ),
    Pin(
        "hub.js",
        f"{_PKG}/hub.js",
        "f6efff9bb477527b4ecd4cf617a3f6473dcea58e37763d431f582c7262bb19c3",
    ),
    Pin(
        "device.js",
        f"{_PKG}/device.js",
        "b261780c034384efb013015012ba197738c06bf66ac54c909d7b67e2c26d446c",
    ),
    Pin(
        "simd.js",
        f"{_PKG}/simd.js",
        "002ae86d173038fe83449b623510c2e697f3837e3a9f39c2c7445525efd9fb3b",
    ),
    Pin(
        "threads.js",
        f"{_PKG}/threads.js",
        "19782737d03b71e028b7924d80cd5d14820f23e28ca352e65975c1a0dfccea0f",
    ),
    Pin(
        "wav.js",
        f"{_PKG}/wav.js",
        "ffe180a940387cf3e5259e68b0a052934b9860034d2f9c82527c986e132d9ce1",
    ),
    Pin(
        "worker.js",
        f"{_PKG}/worker.js",
        "9bfcf650f5d2af09b22268233993395a7182aa50f1d0eb9c01ffa60febb9b632",
    ),
    Pin(
        "wasm/phonon_tts.js",
        f"{_PKG}/wasm/phonon_tts.js",
        "ed6fead3e1e399260883dfb97a6f5d4f666ef7daff248a8cc74cd91cda5478d5",
    ),
    Pin(
        "wasm/phonon_tts_bg.wasm",
        f"{_PKG}/wasm/phonon_tts_bg.wasm",
        "399a92376bbc2c345ee2da775a91f790e32124bc7b4c34faa8180efcf90ff161",
    ),
    Pin(
        "wasm/phonon_tts_simd_bg.wasm",
        f"{_PKG}/wasm/phonon_tts_simd_bg.wasm",
        "a62770e1132de7ea5e1b852de79271a4098c277ae3d84bae12b2f1c6960f1684",
    ),
    Pin(
        "config.json",
        f"{_MODEL}/config.json",
        "b5d0f88353638370d94035b7d73d7d6577c0554d78f8d7ecfd40490aae39ae85",
    ),
    Pin(
        "tokenizer.json",
        f"{_MODEL}/tokenizer.json",
        "691a00068fa7b989f2f3e5ae5daa2d1a5bb319f2d5360deafcfb19a8bb6e7936",
    ),
    Pin(
        "model.q8.gguf",
        f"{_MODEL}/model.q8.gguf",
        "a7d9aad2cd6dc66473f24a08bc6d2939030ef4d751ef82349361fbef3272dbae",
    ),
    Pin(
        "default-voice.safetensors",
        f"{_MODEL}/default-voice.safetensors",
        "dc537478eaadc1a5257fcda2e3501ab81e14c111a5d95a5838f350db2afd0f1a",
    ),
)
MIN_FREE_GB = 1.0


def default_dir() -> Path:
    if d := os.environ.get("AEGIS_SPEECH_DIR"):
        return Path(d)
    cache = os.environ.get("XDG_CACHE_HOME")
    return (Path(cache) if cache else Path.home() / ".cache") / "aegis" / "speech"


class SpeechStore(Store):
    """dictation.Store with Phonon's pins, refusing to start a download on a
    disk short of room."""

    def __init__(
        self, root: Path, pins: tuple[Pin, ...] = PINS, min_free_gb: float = MIN_FREE_GB
    ) -> None:
        super().__init__(root, pins)
        self.min_free_gb = min_free_gb

    async def ensure(self) -> None:
        if not self.ready():
            free = _free_gb(self.root)
            if free is not None and free < self.min_free_gb:
                raise Unavailable(
                    f"the server has {free:.1f} GB free, under the {self.min_free_gb:.1f} GB "
                    "Phonon's 74 MB download needs to leave room for sessions"
                )
        await super().ensure()


def _free_gb(root: Path) -> float | None:
    """Free space on the disk that holds ``root``, or its nearest existing parent."""
    p = root
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return shutil.disk_usage(p).free / 1e9
    except OSError:
        return None
