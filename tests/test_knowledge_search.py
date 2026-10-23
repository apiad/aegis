import time

import pytest

from aegis.knowledge.search import Index

from .knowledge_samples import fixture_hit_at_3, fixture_items, mem, skill


def test_name_description_and_body_weights_rank_a_name_match_first():
    ix = Index(
        [
            mem("deploy-host", "where the bot runs"),
            mem("notes", "deploy mentioned once"),
        ]
    )
    assert ix.rank("deploy", "memory")[0].name == "deploy-host"


def test_accents_and_stems_match_across_spanish_and_english():
    ix = Index([skill("configure-dns", "Configuration of DNS records")])
    assert ix.rank("configuración del dominio", "skill")[0].name == "configure-dns"


def test_different_words_across_languages_do_not_match():
    ix = Index([skill("deploy-bot", "Deploy the bot")])
    assert ix.rank("despliegue", "skill") == []


def test_proposed_and_expired_are_searchable_but_not_hintable():
    ix = Index(
        [
            mem("a", "release freeze", status="proposed"),
            mem("b", "release freeze", expires="2000-01-01"),
        ]
    )
    hits = {h.name: h for h in ix.rank("release", "memory")}
    assert set(hits) == {"a", "b"}
    assert hits["a"].status == "proposed" and hits["b"].flags == ["expired"]
    assert ix.rank("release", "memory", hintable_only=True) == []


def test_paths_double_the_score_in_a_matching_cwd():
    ix = Index(
        [mem("x", "merge rules", paths=["/w/repos/bot"]), mem("y", "merge rules")]
    )
    plain = {h.name: h.score for h in ix.rank("merge", "memory")}
    there = {
        h.name: h.score for h in ix.rank("merge", "memory", cwd="/w/repos/bot/src")
    }
    assert there["x"] == pytest.approx(2 * plain["x"]) and there["y"] == plain["y"]


def test_a_relative_path_glob_matches_under_any_root():
    ix = Index([mem("x", "merge rules", paths=["repos/bot"])])
    plain = ix.rank("merge", "memory")[0].score
    assert ix.rank("merge", "memory", cwd="/home/ana/repos/bot")[
        0
    ].score == pytest.approx(2 * plain)


def test_kinds_rank_apart():
    ix = Index([mem("deploy", "deploy"), skill("deploy-bot", "deploy")])
    assert [h.name for h in ix.rank("deploy", "skill")] == ["deploy-bot"]


def test_a_huge_message_ranks_fast():
    ix = Index(fixture_items())
    t = time.perf_counter()
    ix.rank("log line " * 6000, "skill")
    assert time.perf_counter() - t < 0.05


def test_fixture_bench_hit_at_3():
    hits = fixture_hit_at_3()
    print(f"knowledge hit@3 on the fixture library: {hits:.3f}")
    assert hits >= 0.6, (
        hits
    )  # a floor against regressions; the bench prints the exact value
