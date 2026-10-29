"""Optional text vectors for search, from a fastembed model.

The ``knowledge.embeddings`` config key names the model (``off``, the
default, uses none), and the ``aegis-harness[embeddings]`` extra installs
fastembed. Search then ranks by reciprocal rank fusion of BM25 and cosine
similarity (search.py), never by vectors alone: the spike found every cheap
model level with BM25 and only the fusion with a multilingual one better
(spec, "What the spike measured").

Everything here blocks: the model loads on the first ``vectors`` call,
which takes seconds and about a gigabyte, and each text costs an encode.
So callers run ``vectors`` in a thread, never on the event loop, and the
hint on a send uses only ``cached``, which never loads or encodes.

Vectors are kept unit length as ``array('f')``, so cosine is a dot product
and the default path needs no numpy. Each is cached in memory and on disk
under ``<cache_dir>/<model>/<sha256 of the text>.f32``, so a restart does
not encode the library again. A model that fails to load is logged once
and the embedder turns itself off: search goes on with BM25 alone.
"""

from __future__ import annotations

import hashlib
import importlib.util
import logging
import math
import threading
from array import array
from collections import OrderedDict
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

log = logging.getLogger("aegis.knowledge")

Vector = array
# Loads a model by name; returns what encodes a batch of texts.
Loader = Callable[[str], Callable[[list[str]], Iterable[Sequence[float]]]]
# Query vectors kept in memory, besides the files: the hint reads these.
MEMORY_ITEMS = 4096


def available() -> bool:
    """Whether the embeddings extra is installed, without importing it."""
    return importlib.util.find_spec("fastembed") is not None


def fastembed_loader(model: str) -> Callable[[list[str]], Iterable[Sequence[float]]]:
    # The extra; never imported when off.
    from fastembed import TextEmbedding  # ty: ignore[unresolved-import]

    m = TextEmbedding(model)
    return lambda texts: m.embed(texts)


def unit(v: Iterable[float]) -> Vector:
    out = array("f", v)
    n = math.sqrt(sum(x * x for x in out)) or 1.0
    return array("f", (x / n for x in out))


def cosine(a: Vector, b: Vector) -> float:
    return sum(x * y for x, y in zip(a, b))


class Embedder:
    def __init__(
        self, model: str, cache_dir: Path, load: Loader = fastembed_loader
    ) -> None:
        self.model = model
        self.dir = cache_dir / model.replace("/", "--")
        self._load = load
        self._encode: Callable[[list[str]], Iterable[Sequence[float]]] | None = None
        self._lock = threading.Lock()
        self._memory: OrderedDict[str, Vector] = OrderedDict()
        self._memory_lock = threading.Lock()
        self.broken: str | None = None

    @staticmethod
    def key(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()

    def cached(self, text: str) -> Vector | None:
        """The vector of ``text`` if it is in memory; never blocks."""
        return self._memory.get(self.key(text))

    def _keep(self, key: str, v: Vector) -> None:
        with self._memory_lock:
            self._memory[key] = v
            self._memory.move_to_end(key)
            while len(self._memory) > MEMORY_ITEMS:
                self._memory.popitem(last=False)

    def _read(self, key: str) -> Vector | None:
        try:
            data = (self.dir / f"{key}.f32").read_bytes()
        except OSError:
            return None
        v = array("f")
        v.frombytes(data[: len(data) - len(data) % v.itemsize])
        return v or None

    def _write(self, key: str, v: Vector) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.dir / f".{key}.tmp"
            tmp.write_bytes(v.tobytes())
            tmp.replace(self.dir / f"{key}.f32")
        except OSError as e:
            log.warning("knowledge vectors: not cached: %s", e)

    def vectors(self, texts: list[str]) -> list[Vector] | None:
        """One unit vector per text, or None when the model cannot run.
        Blocks: loads the model the first time and encodes what no cache
        holds. Run it in a thread."""
        if self.broken:
            return None
        keys = [self.key(t) for t in texts]
        out: dict[str, Vector] = {}
        missing: dict[str, str] = {}
        for k, t in zip(keys, texts):
            v = self._memory.get(k) or self._read(k)
            if v is None:
                missing[k] = t
            else:
                out[k] = v
        if missing:
            with self._lock:
                try:
                    if self._encode is None:
                        self._encode = self._load(self.model)
                    got = list(self._encode(list(missing.values())))
                except Exception as e:  # any failure of the extra's model
                    self.broken = f"{type(e).__name__}: {e}"
                    log.warning(
                        "knowledge embeddings off: %s did not run: %s",
                        self.model,
                        self.broken,
                    )
                    return None
            for k, raw in zip(missing, got):
                v = unit(raw)
                self._write(k, v)
                out[k] = v
        for k, v in out.items():
            self._keep(k, v)
        return [out[k] for k in keys]
