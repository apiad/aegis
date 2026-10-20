from pathlib import Path

import pytest

from aegis.claude.control import Model
from aegis.harness_versions import ranges
from aegis.doctor import Found, detect, doctor, propose
from aegis.roots import make_roots

GOOD = """\
default_agent: opus
agents:
  opus: {harness: claude-code, model: opus, effort: high, permission: full}
  pro: {harness: opencode, model: opencode-go/fake-pro, effort: high, permission: full}
queues:
  general: {agent: opus, max_parallel: 3}
"""


def bins(
    claude: str, opencode: str, codex: str = "codex-not-installed"
) -> dict[str, str]:
    return {"claude-code": claude, "opencode": opencode, "codex": codex}


async def run(tmp_path: Path, text: str | None, claude: str, opencode: str):
    if text is not None:
        (tmp_path / ".aegis.yaml").write_text(text)
    return await doctor(make_roots(tmp_path, tmp_path), bins(claude, opencode))


def at(findings, where):
    return [f for f in findings if f.where == where]


async def test_a_healthy_config_has_no_errors_or_warnings(
    tmp_path, fake_claude, fake_opencode
):
    found = await run(tmp_path, GOOD, fake_claude, fake_opencode)
    assert [f for f in found if f.level != "ok"] == []
    (claude,) = at(found, "harness.claude-code")
    max_s = ranges()["claude-code"].max_s
    assert max_s in claude.message and "3 models" in claude.message
    assert {f.where for f in found} >= {
        "file",
        "harness.opencode",
        "agents.opus",
        "agents.pro",
        "default_agent",
        "queues.general",
        "state",
    }


async def test_no_file_says_run_init(tmp_path, fake_claude, fake_opencode):
    (f, *_) = await run(tmp_path, None, fake_claude, fake_opencode)
    assert (f.level, f.where) == ("error", "file") and "aegis init" in f.message


async def test_a_file_that_does_not_parse_is_an_error(
    tmp_path, fake_claude, fake_opencode
):
    (f, *_) = await run(tmp_path, "agents: [1, 2\n", fake_claude, fake_opencode)
    assert (f.level, f.where) == ("error", "file") and "does not parse" in f.message


async def test_unknown_keys_warn_and_a_misspelt_field_is_named(
    tmp_path, fake_claude, fake_opencode
):
    text = "scheduler: {tick_seconds: 5}\nrecap: {agent: a}\ndefault_agent: a\nagents:\n  a: {harness: claude-code, model: opus, efort: high, permission: full}\n"
    found = await run(tmp_path, text, fake_claude, fake_opencode)
    assert at(found, "scheduler")[0].level == "warn"
    # A key recap.py reads, so not an unknown one; its agent is checked (_recap).
    assert [f.level for f in at(found, "recap")] == ["error"]
    assert at(found, "agents.a.efort")[0].level == "warn"
    assert at(found, "agents.a")[0].message == "effort is missing"
    assert at(found, "agents.a")[0].row == "agents.a"


async def test_a_missing_binary_is_an_error_on_the_harness_and_its_agents(
    tmp_path, fake_opencode
):
    text = "default_agent: a\nagents:\n  a: {harness: claude-code, model: opus, effort: high, permission: full}\n"
    found = await run(tmp_path, text, str(tmp_path / "no-claude"), fake_opencode)
    (h,) = at(found, "harness.claude-code")
    assert h.level == "error" and "not on PATH" in h.message
    assert at(found, "agents.a.harness")[0].level == "error"


async def test_a_model_the_catalog_does_not_list_is_a_warning(
    tmp_path, fake_claude, fake_opencode
):
    text = "default_agent: a\nagents:\n  a: {harness: claude-code, model: nope, effort: high, permission: full}\n"
    (f,) = at(await run(tmp_path, text, fake_claude, fake_opencode), "agents.a.model")
    assert f.level == "warn" and "opus" in f.message


async def test_an_effort_the_model_does_not_take_is_a_warning(
    tmp_path, fake_claude, fake_opencode
):
    text = "default_agent: p\nagents:\n  p: {harness: opencode, model: opencode-go/fake-pro, effort: low, permission: full}\n"
    (f,) = at(await run(tmp_path, text, fake_claude, fake_opencode), "agents.p.effort")
    assert f.level == "warn" and "high, max" in f.message


async def test_default_and_queue_problems_are_errors(
    tmp_path, fake_claude, fake_opencode
):
    text = "agents:\n  a: {harness: claude-code, model: opus, effort: high, permission: full}\nqueues:\n  q: {agent: ghost, max_parallel: 1}\n"
    found = await run(tmp_path, text, fake_claude, fake_opencode)
    assert at(found, "default_agent")[0].level == "error"
    assert at(found, "queues.q.agent")[0].level == "error"


async def test_a_valid_recap_is_ok_and_not_an_unknown_key(
    tmp_path, fake_claude, fake_opencode
):
    found = await run(
        tmp_path, GOOD + "recap: {agent: opus}\n", fake_claude, fake_opencode
    )
    assert [f for f in found if f.level != "ok"] == []
    (r,) = at(found, "recap")
    assert r.message == "opus, opus"


async def test_a_recap_naming_no_agent_or_another_harness_is_an_error(
    tmp_path, fake_claude, fake_opencode
):
    for agent, why in (
        ("nope", "does not define"),
        ("pro", "needs a claude-code agent"),
    ):
        text = GOOD + f"recap: {{agent: {agent}}}\n"
        (r,) = at(await run(tmp_path, text, fake_claude, fake_opencode), "recap")
        assert r.level == "error" and why in r.message


async def test_legacy_state_is_an_error_and_the_doctor_writes_nothing(
    tmp_path, fake_claude, fake_opencode
):
    (tmp_path / ".aegis" / "state").mkdir(parents=True)
    (tmp_path / ".aegis" / "state" / "daemon.lock").touch()
    (tmp_path / ".aegis.yaml").write_text(
        "default_agent: a\nagents:\n  a: {harness: claude-code, model: opus, effort: high, permission: full}\n"
    )
    before = sorted(
        p for p in tmp_path.rglob("*") if "bin" not in p.parts and "fake-" not in str(p)
    )
    found = await run(tmp_path, None, fake_claude, fake_opencode)
    assert at(found, "state")[0].level == "error"
    after = sorted(
        p for p in tmp_path.rglob("*") if "bin" not in p.parts and "fake-" not in str(p)
    )
    assert before == after


async def test_detect_finds_both_fakes_with_their_models(
    tmp_path, fake_claude, fake_opencode
):
    claude, opencode, codex = await detect(tmp_path, bins(fake_claude, fake_opencode))
    assert codex.error == "codex-not-installed is not on PATH"
    assert (claude.harness, claude.version, claude.error) == (
        "claude-code",
        f"{ranges()['claude-code'].max_s} (Claude Code)",
        None,
    )
    assert [m.value for m in claude.models] == ["opus", "sonnet", "haiku"]
    assert opencode.models[0].value == "opencode-go/fake-pro"


def m(value: str, efforts=(), free=False) -> Model:
    return Model(
        value=value,
        resolved=value,
        label=value,
        doc="",
        efforts=tuple(efforts),
        free=free,
    )


def oc(*models: Model) -> Found:
    return Found("opencode", "/bin/opencode", "1.18", models)


CLAUDE = Found("claude-code", "/bin/claude", "2.1", (m("opus"),))


@pytest.fixture
def scores(monkeypatch):
    from aegis.ranking import Ranking

    def use(scores: dict) -> None:
        ranked = Ranking.from_dict({"models": scores})
        monkeypatch.setattr("aegis.doctor.ranking", lambda: ranked)

    return use


def test_propose_orders_free_models_by_ranking(scores):
    scores({"o/a": {"coding": 41.2}, "o/b": {"coding": 38.9}})
    doc = propose([oc(m("o/c", free=True), m("o/a", free=True), m("o/b", free=True))])
    (free,) = doc.agents
    assert (free.name, free.harness, free.permission) == ("free", "opencode", "full")
    assert free.models == ["o/a", "o/b", "o/c"] and free.model == "o/a"


def test_propose_puts_no_paid_model_in_a_cascade(scores):
    scores({"g/pro": {"coding": 60.0}, "o/pickle": {"coding": 20.0}})
    paid = m("g/pro", ("high",))
    doc = propose([oc(paid, m("o/pickle", free=True), m("g/max"))])
    (free,) = doc.agents
    assert (free.model, free.models) == ("o/pickle", [])


def test_propose_with_only_paid_models_proposes_the_best_one_alone(scores):
    """Nothing free: the best model alone, as before cascades, so an install
    with only paid OpenCode models still gets an agent."""
    scores({"g/pro": {"coding": 60.0}})
    doc = propose([oc(m("g/max"), m("g/pro", ("high",)))])
    (agent,) = doc.agents
    assert (agent.name, agent.model, agent.models) == ("pro", "g/pro", [])
    assert [q.name for q in doc.queues] == ["general"]
    assert [a.name for a in propose([CLAUDE, oc(m("g/pro"))]).agents] == ["opus", "pro"]


def test_propose_with_claude_adds_free_beside_opus_and_a_light_queue():
    doc = propose([CLAUDE, oc(m("o/a", free=True), m("o/b", free=True))])
    assert [
        (a.name, a.harness, a.model, a.effort, a.permission) for a in doc.agents
    ] == [
        ("opus", "claude-code", "opus", "high", "full"),
        ("free", "opencode", "o/a", "high", "full"),
    ]
    assert doc.default_agent == "opus"
    assert [(q.name, q.agent, q.max_parallel) for q in doc.queues] == [
        ("general", "opus", 3),
        ("light", "free", 3),
    ]


def test_propose_without_claude_makes_free_the_default():
    doc = propose(
        [Found("claude-code", None), oc(m("o/a", free=True), m("o/b", free=True))]
    )
    assert [a.name for a in doc.agents] == ["free"] and doc.default_agent == "free"
    assert [(q.name, q.agent) for q in doc.queues] == [("general", "free")]
    assert propose([Found("claude-code", None), Found("opencode", None)]).agents == []


def test_one_free_model_is_a_cascade_of_one():
    (free,) = propose([oc(m("o/pickle", free=True))]).agents
    assert (free.model, free.models) == ("o/pickle", [])


def test_propose_takes_effort_from_the_first_model(scores):
    scores({"x/y": {"coding": 50.0}})
    doc = propose([oc(m("g/flash", ("low", "medium", "high"), free=True))])
    assert doc.agents[0].effort == "high"
    doc = propose(
        [
            oc(
                m("g/flash", ("high",), free=True),
                m("x/y", ("minimal", "low"), free=True),
            )
        ]
    )
    assert doc.agents[0].effort == "low", "x/y ranks first, so its efforts choose"


async def test_an_opencode_older_than_the_range_warns_with_the_upgrade(
    tmp_path, fake_claude
):
    old = tmp_path / "old" / "opencode"
    old.parent.mkdir()
    old.write_text("#!/bin/sh\necho 1.19.0\n")
    old.chmod(0o755)
    found = await run(tmp_path, GOOD, fake_claude, str(old))
    (oc,) = [f for f in at(found, "harness.opencode") if f.level == "warn"]
    assert "OpenCode 1.19.0 is older than" in oc.message
    assert f"found: {old}" in oc.message
    assert "@opencode/cli@" in oc.message


async def test_a_model_listed_twice_is_a_warning_and_the_repeat_is_dropped(
    tmp_path, fake_claude, fake_opencode
):
    text = "default_agent: p\nagents:\n  p: {harness: opencode, models: [opencode-go/fake-pro, opencode-go/fake-flash, opencode-go/fake-pro], effort: high, permission: full}\n"
    (f,) = at(await run(tmp_path, text, fake_claude, fake_opencode), "agents.p.models")
    assert f.level == "warn"
    assert f.message == "opencode-go/fake-pro is listed twice; the second is ignored"


async def test_a_cascade_out_of_ranking_order_warns(
    tmp_path, fake_claude, fake_opencode, monkeypatch
):
    from aegis.ranking import Ranking

    scores = {"opus": {"coding": 41.2}, "sonnet": {"coding": 38.9}}
    monkeypatch.setattr(
        "aegis.doctor.ranking", lambda: Ranking.from_dict({"models": scores})
    )
    agent = (
        "  {}: {{harness: claude-code, models: [{}], effort: high, permission: full}}\n"
    )
    text = "default_agent: down\nagents:\n" + "".join(
        agent.format(name, models)
        for name, models in (
            ("down", "sonnet, opus"),
            ("unranked", "haiku, opus"),
            ("ordered", "opus, sonnet, haiku"),
        )
    )
    found = await run(tmp_path, text, fake_claude, fake_opencode)
    (f,) = at(found, "agents.down.models")
    assert f.level == "warn" and f.row == "agents.down"
    assert f.message == "sonnet (38.9) is above opus (41.2) in the ranking"
    (f,) = at(found, "agents.unranked.models")
    assert f.message == "haiku (unranked) is above opus (41.2) in the ranking"
    assert at(found, "agents.ordered.models") == []
