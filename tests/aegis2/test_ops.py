import pytest
from pydantic import BaseModel

from aegis2.ops import OpError, Registry


class Echo(BaseModel):
    text: str


def make() -> Registry:
    r = Registry()

    @r.op("echo", Echo)
    async def echo(p: Echo):
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
