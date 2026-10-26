"""The usage page across two real linked servers (test_links.py's pair)."""

from .conftest import until
from .test_links import pair, turn  # noqa: F401


async def test_the_page_totals_both_servers_and_keeps_a_stopped_ones_rows(pair):  # noqa: F811
    alpha, beta = pair
    for node, cost in ((alpha, "0.25"), (beta, "0.5")):
        await turn(await node.spawn(), f"spend {cost}")
        node.app.journal.flush()
    p = await alpha.app.registry.call("usage.page", {"split": "server"})
    assert {n: s["state"] for n, s in p["servers"].items()} == {
        "alpha": "live",
        "beta": "live",
    }
    assert "0.75" in p["tiles"][0]["value"]
    assert {s["key"]: s["text"] for s in p["trend"]["series"]} == {
        "beta": "$0.50",
        "alpha": "$0.25",
    }
    await beta.stop()
    await until(
        lambda: alpha.app.links.get("beta").state != "linked",
        timeout=10,
        what="the link down",
    )
    p = await alpha.app.registry.call("usage.page", {"split": "server"})
    assert p["servers"]["beta"]["state"] == "cached"
    assert p["servers"]["beta"]["age_s"] >= 0
    assert "0.75" in p["tiles"][0]["value"]
