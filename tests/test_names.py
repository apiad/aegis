import random

from aegis.names import PAIRS, default_title, mint_handle, valid_handle


def test_minted_handles_are_valid_and_free():
    taken = set(PAIRS[:-1])
    assert mint_handle(taken) == PAIRS[-1]
    for _ in range(50):
        assert valid_handle(mint_handle(set(), random.Random(1)))


def test_a_full_pool_falls_back_to_a_suffix():
    taken = set(PAIRS)
    h = mint_handle(taken, random.Random(0))
    assert h not in taken and h.endswith("-2") and valid_handle(h)


def test_valid_handle():
    assert valid_handle("quiet-owl") and valid_handle("aegis2-slice-one")
    for bad in ("Quiet-owl", "owl", "a-b-c-d", "1abc-x", "quiet_owl", "quiet-", "-owl"):
        assert not valid_handle(bad), bad


def test_default_title_takes_the_first_line_and_cuts_at_60():
    assert default_title("\n\n  fix the tests  \nand more") == "fix the tests"
    long = "x" * 80
    assert default_title(long) == "x" * 59 + "…"
