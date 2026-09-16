"""Origin answers two questions spawned_by cannot: who made this agent,
and will it outlive the work it was made for."""
import pytest

from aegis.fleet.models import Origin


def test_default_origin_is_the_operator():
    assert Origin().kind == "operator"


@pytest.mark.parametrize("kind", ["queue", "workflow", "group"])
def test_substrate_born_agents_are_ephemeral(kind):
    assert Origin(kind=kind).ephemeral is True


@pytest.mark.parametrize("kind", ["operator", "agent", "fork", "schedule"])
def test_agents_that_outlive_their_task_are_not(kind):
    assert Origin(kind=kind).ephemeral is False


def test_an_unknown_kind_is_not_ephemeral():
    """Ephemerality means the substrate closes it. An unrecognised kind
    has nobody to do that, so guessing 'yes' would ghost a live agent."""
    assert Origin(kind="something-new").ephemeral is False


def test_a_queue_worker_carries_where_its_answer_goes():
    o = Origin(kind="queue", by="general", detail="a3f2", returns_to="rosy-rivest")
    assert (o.by, o.detail, o.returns_to) == ("general", "a3f2", "rosy-rivest")
