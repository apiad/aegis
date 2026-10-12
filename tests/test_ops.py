from aegis.ops import Caller, OpError, Principal, Registry
import pytest
from pydantic import BaseModel


class Echo(BaseModel):
    text: str


def make() -> Registry:
    r = Registry()

    @r.op("echo", Echo, agent=True)
    async def echo(p: Echo, caller):
        if p.text == "boom":
            raise OpError("boom", "asked to fail")
        return p.text.upper()

    return r


async def test_a_valid_call_runs_the_handler():
    assert await make().call("echo", {"text": "hi"}) == "HI"


async def test_bad_params_are_refused_before_the_handler():
    with pytest.raises(OpError) as e:
        await make().call("echo", {"txt": "hi"})
    assert e.value.code == "bad_params"


async def test_unknown_op():
    with pytest.raises(OpError) as e:
        await make().call("nope", {})
    assert e.value.code == "unknown_op"


async def test_handler_errors_keep_their_code():
    with pytest.raises(OpError) as e:
        await make().call("echo", {"text": "boom"})
    assert e.value.code == "boom"


def test_names_and_double_registration():
    r = make()
    assert r.names() == ["echo"]
    with pytest.raises(ValueError):
        r.op("echo", Echo)(lambda p: None)


async def test_agents_reach_only_agent_operations_and_carry_their_session():
    from aegis.ops import Caller

    r = make()

    @r.op("private")
    async def private(p, caller):
        return "secret"

    @r.op("whoami", agent=True)
    async def whoami(p, caller):
        return caller.log_id

    agent = Caller("agent", "log-1")
    assert await r.call("whoami", {}, agent) == "log-1"
    with pytest.raises(OpError) as e:
        await r.call("private", {}, agent)
    assert e.value.code == "not_for_agents"
    assert await r.call("private", {}) == "secret"
    assert [o.tool_name for o in r.agent_ops()] == ["echo", "whoami"]


ANA, BEN = Principal("ana", "admin"), Principal("ben", "member")


class LogParams(BaseModel):
    log_id: str


def registry():
    r = Registry()
    r.owner_of = lambda log_id: {"s-ana": "ana", "s-ben": "ben"}.get(log_id)
    r.who = lambda c: c.principal or BEN  # an agent here acts for ben

    @r.op("admin.thing", admin=True)
    async def admin_thing(p, caller):
        return "done"

    @r.op("session.poke", LogParams, owned="log_id", agent=True)
    async def poke(p, caller):
        return p.log_id

    return r


async def test_an_admin_op_refuses_a_member():
    r = registry()
    assert await r.call("admin.thing", {}, Caller("user", principal=ANA)) == "done"
    with pytest.raises(OpError) as e:
        await r.call("admin.thing", {}, Caller("user", principal=BEN))
    assert e.value.code == "not_for_members"


async def test_an_owned_op_lets_the_owner_and_an_admin_through():
    r = registry()
    assert (
        await r.call("session.poke", {"log_id": "s-ben"}, Caller("user", principal=BEN))
        == "s-ben"
    )
    assert (
        await r.call("session.poke", {"log_id": "s-ben"}, Caller("user", principal=ANA))
        == "s-ben"
    )
    with pytest.raises(OpError) as e:
        await r.call("session.poke", {"log_id": "s-ana"}, Caller("user", principal=BEN))
    assert e.value.code == "not_owner"


async def test_an_agent_is_held_to_its_owners_role():
    r = registry()
    with pytest.raises(OpError) as e:
        await r.call("session.poke", {"log_id": "s-ana"}, Caller("agent", "s-ben"))
    assert e.value.code == "not_owner"


async def test_an_unknown_session_reaches_the_handler():
    r = registry()
    assert (
        await r.call(
            "session.poke", {"log_id": "s-gone"}, Caller("user", principal=BEN)
        )
        == "s-gone"
    )
