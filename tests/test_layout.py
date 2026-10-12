"""The canvas's layout as pure data (layout.py)."""

import pytest

from aegis import layout
from aegis.layout import DEFAULT, LayoutError, ids, normalise, place, resize, trim

ANY = lambda _id: True  # noqa: E731


def win(*columns, n=1):
    return {"n": n, "columns": [{"panes": [{"id": i} for i in col]} for col in columns]}


def test_the_default_is_the_transcript_alone():
    assert ids(None) == ["transcript"] and ids(DEFAULT) == ["transcript"]


def test_widths_and_heights_are_shares_that_add_up_to_one():
    out = normalise({"windows": [win(["transcript"], ["a", "b"])]}, ANY)
    cols = out["windows"][0]["columns"]
    assert [c["w"] for c in cols] == [0.5, 0.5]
    assert [p["h"] for p in cols[1]["panes"]] == [0.5, 0.5]
    out = normalise(
        {
            "windows": [
                {
                    "n": 1,
                    "columns": [
                        {"w": 3, "panes": [{"id": "transcript"}]},
                        {"w": 1, "panes": [{"id": "a", "h": 1}, {"id": "b", "h": 3}]},
                    ],
                }
            ]
        },
        ANY,
    )
    cols = out["windows"][0]["columns"]
    assert [c["w"] for c in cols] == [0.75, 0.25]
    assert [p["h"] for p in cols[1]["panes"]] == [0.25, 0.75]


@pytest.mark.parametrize(
    "windows, says",
    [
        ([win(["a"])], "the transcript"),
        ([win(["transcript"], ["transcript"])], "twice"),
        ([win(["transcript"]), win(["transcript"], n=2)], "twice"),
        ([win(["a"]), win(["transcript"], n=2)], "window 1"),
        ([win(["transcript"], ["a"], ["b"], ["c"], ["d"])], "4 columns"),
        ([win(["transcript", "a", "b", "c"])], "3 panes"),
        (
            [win(["transcript"])] + [win(["x%d" % n], n=n) for n in range(2, 6)],
            "4 windows",
        ),
        ([win(["transcript"]), win(["a"], n=1)], "window 1 twice"),
    ],
)
def test_a_layout_over_a_rule_is_refused_with_the_rule(windows, says):
    with pytest.raises(LayoutError, match=says):
        normalise({"windows": windows}, ANY)


def test_an_id_that_cannot_dock_is_refused_by_name():
    with pytest.raises(LayoutError, match="no pane art-gone"):
        normalise({"windows": [win(["transcript", "art-gone"])]}, lambda i: False)


def test_empty_columns_and_windows_go_and_numbers_stay():
    out = normalise(
        {
            "windows": [
                win(["transcript"], []),
                {"n": 3, "columns": []},
                win(["a"], n=4),
            ]
        },
        ANY,
    )
    assert [w["n"] for w in out["windows"]] == [1, 4]
    assert len(out["windows"][0]["columns"]) == 1


def test_place_at_an_edge_of_window_one():
    lay = place(None, "a", "right", ANY)
    assert ids(lay) == ["transcript", "a"]
    lay = place(lay, "b", "left", ANY)
    assert ids(lay) == ["b", "transcript", "a"]


def test_place_next_to_a_pane_and_move_a_docked_one():
    lay = place(None, "a", "right", ANY)
    lay = place(lay, "b", "below:a", ANY)
    assert [[p["id"] for p in c["panes"]] for c in lay["windows"][0]["columns"]] == [
        ["transcript"],
        ["a", "b"],
    ]
    lay = place(lay, "b", "left:transcript", ANY)  # moved, not copied
    assert ids(lay) == ["b", "transcript", "a"]
    lay = place(lay, "a", "swap:b", ANY)
    assert ids(lay) == ["a", "transcript", "b"]


def test_windows_new_and_numbered():
    lay = place(None, "a", "window:new", ANY)
    assert [w["n"] for w in lay["windows"]] == [1, 2]
    lay = place(lay, "b", "window:2", ANY)
    assert layout.window_of(lay, "b") == 2
    lay = trim(trim(lay, "a"), "b")
    assert [w["n"] for w in lay["windows"]] == [1]
    lay = place(lay, "c", "window:new", ANY)
    assert [w["n"] for w in lay["windows"]] == [1, 2]
    with pytest.raises(LayoutError, match="no window 7"):
        place(lay, "d", "window:7", ANY)


def test_the_transcript_stays_in_window_one():
    lay = place(None, "a", "window:new", ANY)
    with pytest.raises(LayoutError, match="window 1"):
        place(lay, "transcript", "below:a", ANY)


def test_a_bad_where_and_a_missing_target_name_what_exists():
    with pytest.raises(LayoutError, match="dock is left, right"):
        place(None, "a", "middle", ANY)
    with pytest.raises(LayoutError, match="its panes are transcript"):
        place(None, "a", "below:nope", ANY)
    with pytest.raises(LayoutError, match="next to itself"):
        place(place(None, "a", "right", ANY), "a", "below:a", ANY)


def test_a_fifth_column_is_refused_and_the_layout_is_unchanged():
    lay = None
    for i in "abc":
        lay = place(lay, i, "right", ANY)
    before = repr(lay)
    with pytest.raises(LayoutError, match="4 columns"):
        place(lay, "d", "right", ANY)
    assert repr(lay) == before


def test_trim_drops_the_pane_its_column_and_refits():
    lay = place(place(None, "a", "right", ANY), "b", "below:a", ANY)
    lay = trim(lay, "a")
    assert ids(lay) == ["transcript", "b"]
    assert trim(lay, "nope") == lay


def test_resize_sets_one_windows_shares():
    lay = place(place(None, "a", "right", ANY), "b", "below:a", ANY)
    lay = resize(lay, 1, [3, 1], [[1], [1, 3]])
    cols = lay["windows"][0]["columns"]
    assert [c["w"] for c in cols] == [0.75, 0.25]
    assert [p["h"] for p in cols[1]["panes"]] == [0.25, 0.75]
    with pytest.raises(LayoutError, match="2 columns"):
        resize(lay, 1, [1, 1, 1], None)


def test_a_file_key_is_stable_and_short():
    k = layout.file_key("/home/u/out/report.pdf")
    assert k == layout.file_key("/home/u/out/report.pdf")
    assert k.startswith("f-") and len(k) == 14
    assert k != layout.file_key("/home/u/out/other.pdf")


@pytest.mark.parametrize(
    "widths",
    [
        [float("inf"), 1],
        [float("nan"), 1],
        [float("inf"), float("inf")],
        [1e308, 1e308],
        [-1, 1],
    ],
)
def test_a_share_that_is_not_a_finite_positive_number_is_an_equal_one(widths):
    import json
    import math

    cols = [
        {"w": w, "panes": [{"id": i, "h": w}]}
        for w, i in zip(widths, ["transcript", "a"])
    ]
    out = normalise({"windows": [{"n": 1, "columns": cols}]}, ANY)
    shares = [c["w"] for c in out["windows"][0]["columns"]]
    shares += [p["h"] for c in out["windows"][0]["columns"] for p in c["panes"]]
    assert all(math.isfinite(v) and v > 0 for v in shares), shares
    assert abs(sum(c["w"] for c in out["windows"][0]["columns"]) - 1) < 0.001
    json.loads(json.dumps(out, allow_nan=False))  # every browser can parse it


@pytest.mark.parametrize(
    "windows",
    [
        [{"n": 1, "columns": ["transcript"]}],
        [{"n": 1, "columns": [{"panes": ["transcript"]}]}],
        [{"n": 1, "columns": {"panes": []}}],
        [{"n": 1, "columns": [{"panes": {"id": "transcript"}}]}],
    ],
)
def test_a_malformed_layout_is_refused_with_a_reason(windows):
    with pytest.raises(LayoutError):
        normalise({"windows": windows}, ANY)
