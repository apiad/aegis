import re
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

from aegis.claude.control import Model
from aegis.opencode.config import catalog_from
from aegis.ranking import Ranking, Score, annotate, ranking


def small() -> Ranking:
    return Ranking.from_dict(
        {
            "refreshed": "2026-10-18",
            "models": {
                "a": {"aa": "a", "coding": 41.2, "intelligence": 54.0},
                "b": {"aa": "b", "coding": 38.9, "intelligence": 50.1},
                "c": {"aa": "c", "coding": 38.9, "intelligence": 52.0},
                "d": {"aa": "d", "coding": None, "intelligence": 60.0},
            },
        }
    )


def test_order_puts_higher_coding_first_and_unranked_last():
    r = small()
    assert r.order(["x", "b", "y", "a", "d"]) == ["a", "b", "x", "y", "d"]
    assert r.score("a") == Score("a", 41.2, 54.0, None, None)
    assert r.score("x") is None


def test_intelligence_breaks_a_coding_tie():
    assert small().order(["b", "c"]) == ["c", "b"]


def test_rank_counts_only_ranked_models_among_the_given_ones():
    r = small()
    among = ["x", "b", "a", "d"]
    assert r.rank("a", among) == 1
    assert r.rank("b", among) == 2
    assert r.rank("x", among) is None
    assert r.rank("d", among) is None  # no Coding Index: unranked
    assert r.rank("c", among) is None  # ranked, but not among them


def test_the_packaged_file_loads_and_has_a_date():
    r = ranking()
    assert r is ranking()
    # Empty until the first refresh with an Artificial Analysis key.
    assert r.refreshed == "" or re.fullmatch(r"\d{4}-\d{2}-\d{2}", r.refreshed)
    for model in r.models:
        assert r.score(model) is not None


def refresh_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "refresh-model-ranking"
    loader = SourceFileLoader("refresh_model_ranking", str(path))
    module = module_from_spec(spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


def test_slug_for_maps_catalog_ids(monkeypatch):
    s = refresh_script()
    known = {"kimi-k2-5", "glm-5", "claude-opus-5-5", "deepseek-v3-2-reasoning"}
    assert s.slug_for("opencode/kimi-k2.5-free", "", known) == "kimi-k2-5"
    assert s.slug_for("opencode-go/GLM-5", "", known) == "glm-5"
    # Claude Code names an alias; the resolved id is what normalizes.
    assert s.slug_for("opus", "claude-opus-5-5", known) == "claude-opus-5-5"
    assert s.slug_for("Claude Opus 5.5", "", known) == "claude-opus-5-5"
    assert s.slug_for("opencode/big-pickle", "", known) is None
    monkeypatch.setitem(s.ALIASES, "opencode/deepseek-r", "deepseek-v3-2-reasoning")
    assert s.slug_for("opencode/deepseek-r", "", known) == "deepseek-v3-2-reasoning"


def priced() -> Ranking:
    return Ranking.from_dict(
        {
            "refreshed": "2026-10-18",
            "models": {
                "opus": {"aa": "o", "coding": 72.0, "intelligence": 75.3,
                         "price_in": 15.0, "price_out": 75.0},
                "sonnet": {"aa": "s", "coding": 64.1, "intelligence": 68.0,
                           "price_in": 3.0, "price_out": 15.0},
                "go/free": {"aa": "k", "coding": 41.2, "intelligence": 54.0,
                            "price_in": 0.6, "price_out": 2.5},
            },
        }
    )  # fmt: skip


def bare(value: str, **kw) -> Model:
    return Model(value, value, value, "", (), **kw)


def test_annotate_prefers_the_harness_price():
    opus, mine = annotate(
        [bare("opus"), bare("sonnet", price_in=2.0, price_out=9.0)], priced()
    )
    assert (opus.price_in, opus.price_out) == (15.0, 75.0)  # the file's
    assert (mine.price_in, mine.price_out) == (2.0, 9.0)  # the harness's
    assert (opus.coding, opus.intelligence) == (72.0, 75.3)


def test_annotate_ranks_within_the_harness():
    # The ranks count only the models given, which are one harness's catalog.
    sonnet, new, opus = annotate([bare("sonnet"), bare("new"), bare("opus")], priced())
    assert [m.rank for m in (opus, sonnet, new)] == [1, 2, None]
    assert (new.coding, new.price_in) == (None, None)
    (alone,) = annotate([bare("sonnet")], priced())
    assert alone.rank == 1


def test_a_free_model_wires_free_true_and_zero_price():
    cat = catalog_from([], [], [{
        "id": "free", "providerID": "go", "name": "Free",
        "capabilities": {"tools": True, "output": ["text"]},
        "cost": [{"input": 0, "output": 0}],
    }])  # fmt: skip
    (m,) = annotate(cat.models, priced())
    w = m.wire()
    assert (w["free"], w["price_in"], w["price_out"]) == (True, 0, 0)
    assert (w["rank"], w["coding"], w["intelligence"]) == (1, 41.2, 54.0)


def test_annotate_reads_the_packaged_file_by_default():
    (m,) = annotate([bare("no-such/model")])
    assert m.wire()["rank"] is None
