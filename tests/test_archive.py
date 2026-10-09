import pytest

from aegis import archive
from aegis.ops import OpError


def meta(log_id: str, t: float, title: str = "x") -> dict:
    return {"log_id": log_id, "last_activity": t, "title": title, "handle": log_id}


def ids(items) -> list[str]:
    return [m["log_id"] for m in items]


def test_page_orders_newest_first():
    items, total, pos = archive.page(
        [meta("a", 1), meta("b", 3), meta("c", 2)], None, 2, None
    )
    assert ids(items) == ["b", "c"] and total == 3 and pos == (2, "c")


def test_page_after_a_position_skips_nothing_on_ties():
    metas = [meta(f"l{i}", 5.0) for i in range(5)] + [meta("old", 1.0)]
    seen, pos = [], None
    while True:
        items, total, pos = archive.page(metas, None, 2, pos)
        seen += ids(items)
        if pos is None:
            break
    assert sorted(seen) == sorted(ids(metas)) and len(seen) == len(set(seen)) == 6
    assert seen[-1] == "old" and total == 6


def test_the_last_page_says_nothing_is_left():
    items, _, pos = archive.page([meta("a", 1), meta("b", 2)], None, 2, None)
    assert ids(items) == ["b", "a"] and pos is None


def test_page_counts_the_total_matching_the_query():
    metas = [meta(f"l{i}", i, "deploy fix" if i % 2 else "docs") for i in range(10)]
    items, total, _ = archive.page(metas, "DEPLOY", 2, None)
    assert ids(items) == ["l9", "l7"] and total == 5


def test_a_null_last_activity_sorts_as_the_oldest():
    metas = [{"log_id": "z", "last_activity": None}, meta("a", 1)]
    assert ids(archive.page(metas, None, 5, None)[0]) == ["a", "z"]


def test_cursor_round_trips_and_a_bad_one_is_refused():
    positions = {"zion": (12.5, "l1"), "vps": None}
    assert archive.decode(archive.encode(positions)) == positions
    assert archive.decode(None) == {}
    for bad in ("%%%", "bm90IGpzb24", archive.encode({"x": "y"})):  # type: ignore[dict-item]
        with pytest.raises(OpError) as e:
            archive.decode(bad)
        assert e.value.code == "bad_cursor"


def test_merge_takes_the_newest_across_servers_and_keeps_positions():
    zion = [meta("z1", 6), meta("z2", 4), meta("z3", 2)]
    vps = [meta("v1", 5), meta("v2", 3), meta("v3", 1)]
    sources = {"zion": zion, "vps": vps}

    def fetch(positions):
        pages = {}
        for name, metas in sources.items():
            if name in positions and positions[name] is None:
                continue
            items, _, last = archive.page(metas, None, 4, positions.get(name))
            pages[name] = ([{**m, "server": name} for m in items], last)
        return pages

    items, positions = archive.merge(fetch({}), 4, {})
    assert ids(items) == ["z1", "v1", "z2", "v2"]
    more, positions = archive.merge(fetch(positions), 4, positions)
    assert ids(more) == ["z3", "v3"]
    assert positions == {"zion": None, "vps": None}


def test_merge_with_ties_across_servers_repeats_nothing():
    sources = {
        "a": [meta(f"a{i}", 7.0) for i in range(4)],
        "b": [meta(f"b{i}", 7.0) for i in range(3)],
    }
    seen, positions = [], {}
    for _ in range(10):
        pages = {}
        for name, metas in sources.items():
            if name in positions and positions[name] is None:
                continue
            items, _, last = archive.page(metas, None, 3, positions.get(name))
            pages[name] = ([{**m, "server": name} for m in items], last)
        if not pages:
            break
        items, positions = archive.merge(pages, 3, positions)
        seen += ids(items)
    assert sorted(seen) == sorted(ids(sources["a"] + sources["b"])) and len(seen) == 7


async def test_archive_list_pages_with_a_cursor_and_names_the_server(
    tmp_path, fake_claude
):
    import json

    from aegis.app import App
    from aegis.roots import make_roots

    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
    roots = make_roots(tmp_path, None)
    store = roots.state_root / "sessions"
    store.mkdir(parents=True)
    for i in range(7):
        m = {
            "log_id": f"l{i}",
            "handle": f"h-{i}",
            "archived": True,
            "last_activity": 3.0,
        }
        (store / f"l{i}.json").write_text(json.dumps(m))
    app = App(roots, claude_bin=fake_claude, server_name="zion")
    app.sessions.boot()
    seen, cursor, totals = [], None, []
    for _ in range(5):
        r = await app.registry.call("archive.list", {"limit": 3, "cursor": cursor})
        seen += [m["log_id"] for m in r["items"]]
        assert all(m["server"] == "zion" for m in r["items"])
        totals.append((r["total"], r["counts"]))
        cursor = r["cursor"]
        if cursor is None:
            break
    assert sorted(seen) == [f"l{i}" for i in range(7)] and len(seen) == 7
    assert totals[0] == (7, {"zion": 7})
    with pytest.raises(OpError) as e:
        await app.registry.call("archive.list", {"cursor": "%%%"})
    assert e.value.code == "bad_cursor"
