"""Sample items for the knowledge tests, and the public fixture library in
tests/fixtures/knowledge/: a fictional team (ana, ben, cara), 12 skills, 12
memories and 60 labelled queries, half in Spanish. scripts/bench.py reads the
same library for its retrieval row."""

import json
from pathlib import Path

from aegis.knowledge.items import Item, parse_memory, parse_skill
from aegis.knowledge.search import Index

LIBRARY = Path(__file__).parent / "fixtures" / "knowledge"


def mem(name, description="a fact", body="", **meta):
    return Item("memory", name, description, body or f"About {name}.\n", meta)


def skill(name, description="a procedure", body="", **meta):
    return Item("skill", name, description, body or f"1. do {name}\n", meta)


def fixture_items() -> list[Item]:
    mems = [
        parse_memory(p.read_text(), p.stem)
        for p in sorted((LIBRARY / "memories").glob("*.md"))
    ]
    skills = [
        parse_skill(p.read_text(), p.parent.name)
        for p in sorted((LIBRARY / "skills").glob("*/SKILL.md"))
    ]
    return mems + skills


def fixture_hit_at_3() -> float:
    """The share of the library's queries with a labelled item in the top 3."""
    ix = Index(fixture_items())
    queries = json.loads((LIBRARY / "queries.json").read_text())
    hits = sum(
        bool({h.name for h in ix.rank(q["query"], q["kind"], 3)} & set(q["relevant"]))
        for q in queries
    )
    return hits / len(queries)
