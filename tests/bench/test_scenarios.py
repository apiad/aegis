from aegis.bench.scenarios import _drawn_reversed

# Two tab labels as a daemon view drew them after `2` was pressed on the
# fleet screen, from a real run: tab 2 active (SGR 7), tab 3 not.
_BAR = (
    b"\x1b[1;1H\x1b[7;38;2;224;168;114;48;2;26;26;23m\xe2\x97\x8f\x1b[0m"
    b"\x1b[7;38;2;220;217;207;48;2;26;26;23m 2 happy-hoare \x1b[0m"
    b"\x1b[48;2;26;26;23m \x1b[0m\x1b[38;2;224;168;114;48;2;26;26;23m\xe2\x97\x8f\x1b[0m"
    b"\x1b[38;2;220;217;207;48;2;26;26;23m 3 candid-cerf \x1b[0m"
)


class _Rig:
    def __init__(self, *frames: bytes) -> None:
        self._raw_frames = list(frames)


def test_the_active_tab_is_the_one_drawn_in_reverse():
    rig = _Rig(_BAR)
    assert _drawn_reversed(rig, "2 happy-hoare") is True
    assert _drawn_reversed(rig, "3 candid-cerf") is False
    assert _drawn_reversed(rig, "4 neat-naur") is None


def test_a_colour_component_of_7_is_not_reverse_video():
    rig = _Rig(b"\x1b[1;1H\x1b[38;2;7;7;7;48;5;7m 2 happy-hoare \x1b[0m")
    assert _drawn_reversed(rig, "2 happy-hoare") is False


def test_the_newest_frame_wins():
    before = b"\x1b[1;1H\x1b[38;2;1;1;1m 2 happy-hoare \x1b[0m"
    assert _drawn_reversed(_Rig(before, _BAR), "2 happy-hoare") is True
    assert _drawn_reversed(_Rig(_BAR, before), "2 happy-hoare") is False
