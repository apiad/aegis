# ruff: noqa: F811  (pytest fixtures imported from other test modules)
"""The optional embeddings (knowledge/embed.py), with a fake model, so CI
downloads nothing: fusion by rank with BM25, the vector cache, a send that
never waits for the model, and nothing imported when they are off."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time

from aegis import doctor
from aegis.knowledge import embed
from aegis.knowledge.embed import Embedder, unit
from aegis.knowledge.search import Index, terms, text_of
from aegis.knowledge.service import Knowledge

from .conftest import until
from .knowledge_samples import mem
from .test_agents import mcp, turn, world  # noqa: F401  (fixture)
from .test_doctor import at, run

# The fake model's meaning: words that share a concept share a dimension,
# across languages, which BM25's stems cannot do.
CONCEPTS = {
    "deplo": 0,
    "despl": 0,
    "ship": 0,
    "bot": 1,
    "chat": 1,
    "host": 2,
    "serve": 2,
    "servi": 2,
    "backu": 3,
    "respa": 3,
}
DIMS = 6


def fake_vector(text: str) -> list[float]:
    v = [0.0] * DIMS
    for t in terms(text):
        v[CONCEPTS.get(t, 4 + len(t) % 2)] += 1.0 if t in CONCEPTS else 0.1
    return v


class FakeModel:
    """A loader for Embedder: counts loads and encoded texts, and can be
    held at load until ``go`` is set."""

    def __init__(self, hold: bool = False) -> None:
        self.loads = 0
        self.encoded: list[str] = []
        self.go = threading.Event()
        if not hold:
            self.go.set()

    def __call__(self, model: str):
        self.loads += 1
        self.go.wait(10)

        def encode(texts: list[str]):
            self.encoded += texts
            return [fake_vector(t) for t in texts]

        return encode


def vectors_of(items):
    return {i.name: unit(fake_vector(text_of(i))) for i in items}


def test_fusion_uses_both_ranks_with_k_60():
    items = [
        mem("bot-host", "The chat bot runs on host prod-1"),
        mem("bot-backup", "The bot's database backup runs nightly"),
        mem("deploy-steps", "Deploy with docker compose up"),
    ]
    q = "where does the bot run"
    bm25 = Index(items).rank(q, "memory", 5)
    fused = Index(items, vectors_of(items)).rank(
        q, "memory", 5, query_vector=unit(fake_vector(q))
    )
    bm25_rank = {h.name: r for r, h in enumerate(bm25, 1)}
    qv = unit(fake_vector(q))
    near = sorted(
        items, key=lambda i: (-embed.cosine(qv, unit(fake_vector(text_of(i)))), i.name)
    )
    cos_rank = {i.name: r for r, i in enumerate(near, 1)}
    for h in fused:
        want = 1 / (60 + cos_rank[h.name])
        if h.name in bm25_rank:
            want += 1 / (60 + bm25_rank[h.name])
        assert abs(h.score - want) < 1e-9, h
    # Without the query's vector, the same index ranks by BM25 alone.
    assert Index(items, vectors_of(items)).rank(q, "memory", 5) == bm25


def test_fusion_finds_what_bm25_cannot_across_languages():
    items = [
        mem("deploy-bot", "Deploy the chat bot with docker compose"),
        mem("cara-timezone", "Cara answers messages after 14:00 UTC"),
    ]
    q = "despliegue del bot"  # "bot" matches; "despliegue" never meets "deploy"
    only = [mem("ship-it", "Ship the chat bot"), items[1]]
    assert Index(only).rank("despliegue", "memory", 5) == []
    fused = Index(only, vectors_of(only)).rank(
        "despliegue", "memory", 5, query_vector=unit(fake_vector("despliegue"))
    )
    assert fused[0].name == "ship-it"
    assert Index(items, vectors_of(items)).rank(
        q, "memory", 1, query_vector=unit(fake_vector(q))
    )[0].name == ("deploy-bot")


def test_vectors_are_cached_by_content(tmp_path):
    model = FakeModel()
    e = Embedder("org/fake-model", tmp_path, load=model)
    a, b = e.vectors(["deploy the bot", "the backup"])
    assert model.loads == 1 and model.encoded == ["deploy the bot", "the backup"]
    assert e.vectors(["the backup", "deploy the bot"]) == [b, a]
    assert model.encoded == ["deploy the bot", "the backup"]  # nothing again
    files = sorted(p.name for p in (tmp_path / "org--fake-model").iterdir())
    assert files == sorted(
        f"{Embedder.key(t)}.f32" for t in ("deploy the bot", "the backup")
    )
    again = FakeModel()
    fresh = Embedder("org/fake-model", tmp_path, load=again)
    assert fresh.vectors(["deploy the bot"]) == [a]
    assert again.loads == 0  # read from disk: the model never loaded
    assert fresh.cached("deploy the bot") == a and fresh.cached("other") is None
    fresh.vectors(["the backup, edited"])
    assert again.encoded == ["the backup, edited"]  # a new text, a new vector


def test_a_model_that_fails_turns_the_embedder_off(tmp_path):
    def broken(model):
        raise RuntimeError("no such model")

    e = Embedder("org/missing", tmp_path, load=broken)
    assert e.vectors(["anything"]) is None and "no such model" in (e.broken or "")
    kn = Knowledge(tmp_path / "state", "org/missing", load=broken)

    async def go():
        await kn.store.save("ana", mem("bot-host", "The bot runs on host b"), "t")
        ix = await kn.index("ana")
        assert ix.vectors is None  # BM25 alone
        assert kn.hint_for("ana", "s", "where is the bot", None)
        kn.usage.stop()

    asyncio.run(go())


async def test_a_send_never_waits_for_the_model(tmp_path):
    model = FakeModel(hold=True)
    kn = Knowledge(tmp_path, "org/fake-model", load=model)
    await kn.store.save("ana", mem("ship-it", "Ship the chat bot to prod"), "t")
    await kn.store.save("ana", mem("cara-tz", "Cara answers after 14:00 UTC"), "t")
    t0 = time.monotonic()
    first = kn.hint_for("ana", "s", "despliegue", None)
    assert time.monotonic() - t0 < 1.0  # the model is still loading
    assert first == ""  # BM25 alone: no word is shared
    await until(lambda: model.loads == 1, timeout=5, what="the model's load")
    model.go.set()
    await until(
        lambda: kn._indexes["ana"][3].vectors is not None,
        timeout=5,
        what="vectors in the index",
    )
    # This send's text has no vector yet: BM25 alone, and it is encoded.
    assert kn.hint_for("ana", "s", "despliegue", None) == ""
    await until(
        lambda: kn.embedder and kn.embedder.cached("despliegue") is not None,
        timeout=5,
        what="the query's vector",
    )
    assert "ship-it" in kn.hint_for("ana", "s", "despliegue", None)
    kn.usage.stop()


async def test_search_tools_await_the_query_vector(world):
    app = world.app
    app.knowledge.embedder = Embedder(
        "org/fake-model", app.roots.state_root / "knowledge-vectors", load=FakeModel()
    )
    await app.knowledge.store.save(app.user, mem("ship-it", "Ship the chat bot"), "t")
    s = await world.spawn()
    said = await turn(s, mcp("memory_search", query="despliegue"))
    assert said.startswith("mcp ok: "), said
    hits = json.loads(said.removeprefix("mcp ok: "))["hits"]
    assert [h["name"] for h in hits] == ["ship-it"]


def test_off_imports_no_numpy(tmp_path):
    code = f"""
import asyncio, sys
from pathlib import Path
import aegis.app, aegis.doctor
from aegis.knowledge.service import Knowledge
from aegis.knowledge.items import Item

async def go():
    for setting in ("off", "org/model-without-the-extra"):
        kn = Knowledge(Path({str(tmp_path)!r}) / setting.replace("/", "-"), setting)
        await kn.store.save("ana", Item("memory", "bot-host", "the bot host", "b"), "t")
        await kn.index("ana")
        kn.hint_for("ana", "s", "where is the bot", None)
        kn.usage.stop()
        assert kn.embedder is None, setting

asyncio.run(go())
bad = sorted(m for m in sys.modules if m.split(".")[0] in ("numpy", "fastembed", "onnxruntime"))
print(bad)
"""
    # A numpy the process could import, so an import of it would show.
    fake = tmp_path / "importable"
    (fake / "numpy").mkdir(parents=True)
    (fake / "numpy" / "__init__.py").write_text("")
    env = {**os.environ, "PYTHONPATH": str(fake)}
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"


async def test_doctor_warns_without_the_extra(
    tmp_path, fake_claude, fake_opencode, monkeypatch
):
    from .test_doctor import GOOD

    text = GOOD + "knowledge: {embeddings: minishlab/potion-multilingual-128M}\n"
    monkeypatch.setattr(embed, "available", lambda: False)
    (f,) = at(
        await run(tmp_path, text, fake_claude, fake_opencode), "knowledge.embeddings"
    )
    assert f.level == "warn" and "aegis-harness[embeddings]" in f.message
    monkeypatch.setattr(embed, "available", lambda: True)
    (f,) = at(
        await run(tmp_path, text, fake_claude, fake_opencode), "knowledge.embeddings"
    )
    assert f.level == "ok"
    off = await run(tmp_path, GOOD, fake_claude, fake_opencode)
    assert at(off, "knowledge.embeddings") == []
    bad = GOOD + "knowledge: {embedings: x}\n"
    (f,) = at(await run(tmp_path, bad, fake_claude, fake_opencode), "knowledge")
    assert f.level == "error" and "embedings" in f.message
    assert doctor._embeddings({"embeddings": "off"}) == []
