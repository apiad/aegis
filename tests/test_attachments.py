from pathlib import Path

import pytest

from aegis import attachments as att, files
from aegis.files import FileError, find


def staged(state, log_id="log1"):
    return att.inbox(state, log_id) / att.STAGED


def up(state, body, name="a.bin", log_id="log1", chunk=4):
    uid = att.begin(state, log_id, name, len(body))
    for off in range(0, len(body), chunk):
        att.put(state, log_id, uid, off, body[off : off + chunk])
    return uid


def test_chunks_land_in_staging_and_a_commit_moves_them_into_the_inbox(tmp_path):
    uid = up(tmp_path, b"0123456789", "data.bin")
    assert (staged(tmp_path) / uid / "data.bin").read_bytes() == b"0123456789"
    (rec,) = att.commit(tmp_path, "log1", [uid], now=1791650102.0)
    dest = Path(rec["path"])
    assert dest.parent == att.inbox(tmp_path, "log1")
    assert dest.name.endswith("-data.bin")
    assert dest.name[:15].replace("-", "").isdigit()
    assert dest.read_bytes() == b"0123456789"
    assert not (staged(tmp_path) / uid).exists()
    assert rec["name"] == "data.bin" and rec["size"] == 10


def test_an_agent_editing_its_inbox_copy_leaves_the_sent_card_alone(tmp_path):
    (rec,) = att.commit(tmp_path, "log1", [up(tmp_path, b"png", "a.png")])
    served = find(tmp_path, rec["file_id"], "a.png")
    assert served.read_bytes() == b"png" and rec["preview"] == "image"
    with open(rec["path"], "ab") as f:  # the agent appends, in place
        f.write(b" edited")
    assert served.read_bytes() == b"png"


def test_a_resent_chunk_is_acknowledged_and_not_written_twice(tmp_path):
    uid = att.begin(tmp_path, "log1", "a.bin", 8)
    assert att.put(tmp_path, "log1", uid, 0, b"abcd") == 4
    assert att.put(tmp_path, "log1", uid, 0, b"abcd") == 4
    assert att.put(tmp_path, "log1", uid, 4, b"efgh") == 8
    assert (staged(tmp_path) / uid / "a.bin").read_bytes() == b"abcdefgh"


@pytest.mark.parametrize(
    "offset, data, code",
    [
        (6, b"zz", "bad_offset"),
        (2, b"cdefghij", "bad_offset"),
        (4, b"efghi", "too_large"),
    ],
)
def test_a_gap_an_overlap_or_an_overrun_is_refused(tmp_path, offset, data, code):
    uid = att.begin(tmp_path, "log1", "a.bin", 8)
    att.put(tmp_path, "log1", uid, 0, b"abcd")
    with pytest.raises(FileError) as e:
        att.put(tmp_path, "log1", uid, offset, data)
    assert e.value.code == code


def test_a_file_over_the_cap_is_refused_before_it_uploads(tmp_path):
    with pytest.raises(FileError) as e:
        att.begin(tmp_path, "log1", "big.bin", files.MAX_BYTES + 1)
    assert e.value.code == "too_large"


def test_an_incomplete_upload_refuses_the_commit_and_moves_nothing(tmp_path):
    done = up(tmp_path, b"full", "a.bin")
    half = att.begin(tmp_path, "log1", "b.bin", 10)
    att.put(tmp_path, "log1", half, 0, b"part")
    with pytest.raises(FileError) as e:
        att.commit(tmp_path, "log1", [done, half])
    assert e.value.code == "upload_incomplete"
    assert (staged(tmp_path) / done / "a.bin").exists()
    assert not list(att.inbox(tmp_path, "log1").glob("*-a.bin"))


def test_two_files_with_one_name_in_one_second_both_survive(tmp_path):
    a, b = up(tmp_path, b"one", "image.png"), up(tmp_path, b"two", "image.png")
    recs = att.commit(tmp_path, "log1", [a, b], now=1791650102.0)
    names = [Path(r["path"]).name for r in recs]
    assert names[1] == names[0].replace(".png", "-2.png")
    assert [Path(r["path"]).read_bytes() for r in recs] == [b"one", b"two"]


@pytest.mark.parametrize(
    "raw, clean",
    [
        ("shot.png", "shot.png"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\a\\note.txt", "note.txt"),
        (".bashrc", "_bashrc"),
        ("a\x00b\x1fc.txt", "abc.txt"),
        ("x" * 300 + ".png", "x" * 116 + ".png"),
    ],
)
def test_names_are_cleaned(raw, clean):
    assert att.clean_name(raw) == clean


@pytest.mark.parametrize("raw", ["", "/", "\x00"])
def test_a_name_with_nothing_left_is_refused(raw):
    with pytest.raises(FileError) as e:
        att.clean_name(raw)
    assert e.value.code == "bad_name"


@pytest.mark.parametrize("uid", ["../../files", "short", "a" * 22 + "/x", "a" * 22])
def test_a_malformed_or_unknown_upload_id_is_refused(tmp_path, uid):
    with pytest.raises(FileError) as e:
        att.put(tmp_path, "log1", uid, 0, b"x")
    assert e.value.code == "no_upload"


def test_dropping_an_upload_removes_its_staging(tmp_path):
    uid = up(tmp_path, b"abc")
    att.drop(tmp_path, "log1", uid)
    assert not (staged(tmp_path) / uid).exists()


def test_clearing_staging_keeps_sent_files(tmp_path):
    (rec,) = att.commit(tmp_path, "log1", [up(tmp_path, b"kept")])
    up(tmp_path, b"left", log_id="log2")
    att.clear_staged(tmp_path)
    assert Path(rec["path"]).read_bytes() == b"kept"
    assert not list((tmp_path / "inbox").glob("*/.staged"))


def test_the_inbox_is_private(tmp_path):
    up(tmp_path, b"x")
    assert att.inbox(tmp_path, "log1").stat().st_mode & 0o777 == 0o700


def test_the_message_puts_the_files_after_the_text():
    sent = [
        {
            "path": "/s/inbox/l/20261010-101502-a.png",
            "mime": "image/png",
            "size": 319488,
        }
    ]
    assert att.message("look", sent) == (
        "look\n\nAttached files:\n- /s/inbox/l/20261010-101502-a.png (image/png, 312 KB)"
    )
    assert att.message("", sent).startswith("Attached files:\n- ")
    assert att.message("look", []) == "look"
