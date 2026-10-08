"""The server's half of dictation: the pinned engine and model, and the keywords.

Speech is transcribed in the browser (DESIGN.md, "Audio never leaves the
browser"). The server keeps Cactus Whistle's browser build on disk, verified
against the hashes below, serves it at ``/dictation/<pin>/<name>`` with an
immutable cache header (web.py), and tells the client which words to bias the
model toward. The files come from Hugging Face at fixed revisions, and a new
pin is a new directory, so a cache never mixes two versions. A directory that
already holds the files is trusted: aegis checked each one when it wrote it, and
hashing 17.8 MB on every press would cost more than the check is worth.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import httpx


@dataclass(frozen=True)
class Pin:
    name: str
    url: str
    sha256: str


_ENGINE = "https://huggingface.co/Cactus-Compute/needle3/resolve/c7c415a3d1b3d929014bc6e866d51ebb971f7089/wasm"
_MODEL = "https://huggingface.co/Cactus-Compute/whistle/resolve/b358ddadd89b7a713b5aa131f23032d3cca1b251"
PINS = (
    Pin(
        "needle.js",
        f"{_ENGINE}/needle.js",
        "f3f7366dcad9555b792ee519d2518f3c506038bcb2ffd179e76e850000749359",
    ),
    Pin(
        "needle.wasm",
        f"{_ENGINE}/needle.wasm",
        "c19b9ddf9c7de4eb4f37e5f1811c5bbea9f099041d2a27284daf89789ee8523d",
    ),
    Pin(
        "whistle.cact",
        f"{_MODEL}/whistle.cact",
        "b6e02f048568ac5d01a2042556c658061e699acbc0aa2a1439f52f3d461dffeb",
    ),
)

# Words of agent work that Whistle misses inside Spanish speech without a hint.
VOCABULARY = (
    "agent",
    "agents",
    "harness",
    "issue",
    "pull request",
    "PR",
    "repo",
    "worktree",
    "branch",
    "commit",
    "merge",
    "CI",
    "aegis",
    "Claude",
    "Claude Code",
    "OpenCode",
    "MCP",
    "monitor",
    "queue",
    "handoff",
    "session",
    "subagent",
    "spec",
    "plan",
    "transcript",
)
# 300 keywords cost no more decode time than 16 (the spec's measurement).
MAX_KEYWORDS = 300
# httpx's timeout is per operation, so a slow stream never ends without this.
DOWNLOAD_TIMEOUT_S = 900.0


class Unavailable(Exception):
    """The files could not be fetched, stored, or did not match their hashes."""


def pin_id(pins: tuple[Pin, ...] = PINS) -> str:
    return hashlib.sha256("".join(p.sha256 for p in pins).encode()).hexdigest()[:12]


def default_dir() -> Path:
    if d := os.environ.get("AEGIS_DICTATION_DIR"):
        return Path(d)
    cache = os.environ.get("XDG_CACHE_HOME")
    return (Path(cache) if cache else Path.home() / ".cache") / "aegis" / "dictation"


class Store:
    def __init__(
        self,
        root: Path,
        pins: tuple[Pin, ...] = PINS,
        timeout_s: float = DOWNLOAD_TIMEOUT_S,
    ) -> None:
        self.root, self.pins, self.timeout_s = root, pins, timeout_s
        self._task: asyncio.Task | None = None

    @property
    def id(self) -> str:
        return pin_id(self.pins)

    @property
    def dir(self) -> Path:
        return self.root / self.id

    def ready(self) -> bool:
        return all((self.dir / p.name).is_file() for p in self.pins)

    def file(self, pin: str, name: str) -> Path | None:
        if pin != self.id or name not in {p.name for p in self.pins}:
            return None
        path = self.dir / name
        return path if path.is_file() else None

    async def ensure(self) -> None:
        """The files on disk, downloading what is missing. Concurrent callers
        share one download, which no single caller's cancellation stops; one
        that failed is retried by the next call."""
        if self.ready():
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._download_all())
        await asyncio.shield(self._task)

    async def _download_all(self) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise Unavailable(f"cache directory {self.dir}: {e}") from e
        async with httpx.AsyncClient(follow_redirects=True) as client:
            for p in self.pins:
                if (self.dir / p.name).is_file():
                    continue
                try:
                    await asyncio.wait_for(self._download(client, p), self.timeout_s)
                except TimeoutError:
                    raise Unavailable(
                        f"{p.name}: not downloaded within {self.timeout_s:.0f} s"
                    ) from None

    async def _download(self, client: httpx.AsyncClient, p: Pin) -> None:
        part = self.dir / f".{p.name}.part"
        digest = hashlib.sha256()
        try:
            async with client.stream("GET", p.url) as r:
                if r.status_code != 200:
                    raise Unavailable(f"{p.name}: HTTP {r.status_code}")
                with part.open("wb") as f:
                    async for chunk in r.aiter_bytes():
                        digest.update(chunk)
                        f.write(chunk)
            if digest.hexdigest() != p.sha256:
                raise Unavailable(f"{p.name}: sha256 does not match the pin")
            part.replace(self.dir / p.name)
        except httpx.HTTPError as e:
            raise Unavailable(f"{p.name}: {e}") from e
        except OSError as e:
            raise Unavailable(f"cache directory {self.dir}: {e}") from e
        finally:
            part.unlink(missing_ok=True)


def keywords(
    handles: Iterable[str | None],
    cwds: Iterable[Path | str],
    names: Iterable[str],
    limit: int = MAX_KEYWORDS,
) -> list[str]:
    """The phrases Whistle is biased toward: the vocabulary, then what this
    server knows by name. A handle is spoken with spaces, not hyphens."""
    out: list[str] = []
    seen: set[str] = set()
    words = (
        *VOCABULARY,
        *((h or "").replace("-", " ") for h in handles),
        *(Path(c).name for c in cwds),
        *names,
    )
    for w in words:
        w = " ".join(w.split())
        if w and w.lower() not in seen and len(out) < limit:
            seen.add(w.lower())
            out.append(w)
    return out
