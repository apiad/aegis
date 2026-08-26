"""The digest's pure half: what a turn did, and how it reads in a prompt."""
from aegis.digest.models import CommitLine, RepoDelta, TurnFacts
from aegis.digest.render import render_facts


def _commit(sha="abc1234", subject="feat: a thing"):
    return CommitLine(sha=sha, subject=subject)


def test_moved_is_false_for_a_read_only_turn():
    """The gate the recap hangs on. A turn of questions moved nothing."""
    assert TurnFacts().moved is False
    assert TurnFacts(assistant_tail="I read the file.").moved is False


def test_moved_is_true_on_a_commit():
    facts = TurnFacts(repos=(RepoDelta(name="aegis",
                                       commits=(_commit(),)),))
    assert facts.moved is True


def test_moved_is_true_on_a_write_with_no_commit():
    facts = TurnFacts(repos=(RepoDelta(name="aegis", files_written=3),))
    assert facts.moved is True


def test_moved_is_true_on_plan_progress_alone():
    """A turn that finished a task without touching a tracked repo."""
    assert TurnFacts(plan_done_delta=1, plan_done=3, plan_total=5).moved


def test_moved_is_false_when_the_digest_failed():
    """An errored digest must not read as movement — that would make every
    broken collection fire a recap."""
    facts = TurnFacts(repos=(RepoDelta(name="aegis", files_written=3),),
                      error="git exploded")
    assert facts.moved is False


def test_render_names_commits_and_counts():
    facts = TurnFacts(
        repos=(RepoDelta(name="aegis", files_written=2,
                         commits=(_commit("51430de", "docs(spec): the spec"),
                                  _commit("dae9d19", "docs: identity"))),),
        plan_done_delta=1, plan_done=2, plan_total=5)
    out = render_facts(facts)
    assert "aegis" in out
    assert "51430de" in out and "docs(spec): the spec" in out
    assert "2 files" in out
    assert "2/5" in out


def test_render_of_nothing_is_explicit_not_empty():
    """An empty string would read as 'facts unavailable'. It is not the
    same claim as 'this turn changed nothing', and the judge acts on the
    difference."""
    out = render_facts(TurnFacts())
    assert out.strip()
    assert "no commits" in out.lower() or "nothing" in out.lower()


def test_render_says_so_when_a_repo_is_off_host():
    facts = TurnFacts(repos=(RepoDelta(name="app", host="vps",
                                       available=False),))
    out = render_facts(facts)
    assert "vps" in out
    assert "not inspected" in out.lower() or "unavailable" in out.lower()


def test_render_surfaces_the_error():
    out = render_facts(TurnFacts(error="git not found"))
    assert "git not found" in out
