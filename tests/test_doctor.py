from pathlib import Path

from aegis.claude.control import Model
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
    assert "0.0-fake" in claude.message and "3 models" in claude.message
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
    text = "scheduler: {tick_seconds: 5}\ndefault_agent: a\nagents:\n  a: {harness: claude-code, model: opus, efort: high, permission: full}\n"
    found = await run(tmp_path, text, fake_claude, fake_opencode)
    assert at(found, "scheduler")[0].level == "warn"
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
        "0.0-fake (Claude Code)",
        None,
    )
    assert [m.value for m in claude.models] == ["opus", "sonnet", "haiku"]
    assert opencode.models[0].value == "opencode-go/fake-pro"


def m(value: str, efforts=()) -> Model:
    return Model(
        value=value, resolved=value, label=value, doc="", efforts=tuple(efforts)
    )


def test_propose_claude_first_then_opencode():
    doc = propose(
        [
            Found("claude-code", "/bin/claude", "2.1", (m("opus"),)),
            Found(
                "opencode",
                "/bin/opencode",
                "1.18",
                (m("opencode-go/deepseek-v4-pro", ("minimal", "high", "max")),),
            ),
        ]
    )
    assert [
        (a.name, a.harness, a.model, a.effort, a.permission) for a in doc.agents
    ] == [
        ("opus", "claude-code", "opus", "high", "full"),
        ("deepseek-v4-pro", "opencode", "opencode-go/deepseek-v4-pro", "high", "full"),
    ]
    assert doc.default_agent == "opus"
    assert [(q.name, q.agent, q.max_parallel) for q in doc.queues] == [
        ("general", "opus", 3)
    ]


def test_propose_without_claude_defaults_to_opencode_and_nothing_installed_proposes_nothing():
    doc = propose(
        [
            Found("claude-code", None),
            Found("opencode", "/bin/opencode", "1.18", (m("opencode-go/opus"),)),
        ]
    )
    assert doc.default_agent == "opus" and doc.agents[0].harness == "opencode"
    assert propose([Found("claude-code", None), Found("opencode", None)]).agents == []


def test_propose_prefers_effort_high_when_the_model_offers_it():
    doc = propose(
        [
            Found(
                "opencode",
                "/bin/opencode",
                "1.18",
                (m("google/gemini-flash", ("low", "medium", "high")),),
            )
        ]
    )
    assert doc.agents[0].effort == "high"
    doc = propose(
        [Found("opencode", "/bin/opencode", "1.18", (m("x/y", ("minimal", "low")),))]
    )
    assert doc.agents[0].effort == "low"


def test_propose_takes_the_first_free_opencode_model():
    paid = Model(value="g/pro", resolved="g/pro", label="", doc="", efforts=("high",))
    free = Model(
        value="o/pickle", resolved="o/pickle", label="", doc="", efforts=(), free=True
    )
    doc = propose([Found("opencode", "/bin/opencode", "1.18", (paid, free))])
    assert (doc.agents[0].name, doc.agents[0].model) == ("pickle", "o/pickle")
    doc = propose([Found("opencode", "/bin/opencode", "1.18", (paid,))])
    assert doc.agents[0].model == "g/pro", "with nothing free, the first model"
