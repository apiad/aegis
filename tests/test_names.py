import random
import re
from aegis.tui.names import generate_name, ADJECTIVES, LAUREATES


def test_format_and_membership():
    n = generate_name(set(), random.Random(0))
    adj, last = n.split("-")
    assert adj in ADJECTIVES and last in LAUREATES
    assert re.fullmatch(r"[a-z]+-[a-z]+", n)


def test_seeded_deterministic():
    assert generate_name(set(), random.Random(7)) == \
           generate_name(set(), random.Random(7))


def test_never_returns_taken():
    taken = {f"{a}-{l}" for a in ADJECTIVES for l in LAUREATES} - {"lucid-knuth"}
    assert generate_name(taken, random.Random(1)) == "lucid-knuth"


def test_numeric_suffix_fallback(monkeypatch):
    monkeypatch.setattr("aegis.tui.names.ADJECTIVES", ["only"])
    monkeypatch.setattr("aegis.tui.names.LAUREATES", ["one"])
    n = generate_name({"only-one"}, random.Random(0))
    assert n == "only-one-2"
