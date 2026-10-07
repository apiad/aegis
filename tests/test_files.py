import pytest

from aegis import files
from aegis.files import FileError, classify, excerpt, find, store, url


@pytest.mark.parametrize(
    "name, mime, preview",
    [
        ("a.png", "image/png", "image"),
        ("a.svg", "image/svg+xml", "image"),
        ("a.pdf", "application/pdf", "pdf"),
        ("a.html", "text/html", "html"),
        ("a.md", "text/markdown", "markdown"),
        ("a.py", "text/x-python", "text"),
        ("a.csv", "text/csv", "text"),
        ("a.json", "application/json", "text"),
        ("a.mp3", "audio/mpeg", "audio"),
        ("a.mp4", "video/mp4", "video"),
        ("a.zip", "application/zip", "other"),
    ],
)
def test_the_name_decides_the_preview(tmp_path, name, mime, preview):
    p = tmp_path / name
    p.write_bytes(b"x")
    assert classify(p) == (mime, preview)


def test_a_file_without_an_extension_is_sniffed(tmp_path):
    text, blob = tmp_path / "notes", tmp_path / "blob"
    text.write_text("plain words, año\n")
    blob.write_bytes(b"\x89PNG\x00\x01\x02")
    assert classify(text) == ("text/plain", "text")
    assert classify(blob) == ("application/octet-stream", "other")
    assert store(tmp_path, blob)["excerpt"] is None


def test_the_excerpt_is_40_lines_or_4_kb_cut_at_a_line(tmp_path):
    many = tmp_path / "many.txt"
    many.write_text("".join(f"line {i}\n" for i in range(100)))
    assert excerpt(many).splitlines() == [f"line {i}" for i in range(40)]
    wide = tmp_path / "wide.txt"
    wide.write_text("".join("x" * 1000 + "\n" for _ in range(10)))
    got = excerpt(wide)
    assert len(got.encode()) <= 4096 and got.endswith("x") and got.count("\n") == 3


def test_store_copies_and_survives_the_original(tmp_path):
    src = tmp_path / "chart.png"
    src.write_bytes(b"png bytes")
    rec = store(tmp_path / "state", src)
    src.unlink()
    assert rec["kind"] == "file" and rec["name"] == "chart.png"
    assert rec["size"] == 9 and rec["preview"] == "image"
    path = find(tmp_path / "state", rec["file_id"], "chart.png")
    assert path.read_bytes() == b"png bytes"
    assert not list((tmp_path / "state" / "files").glob(".*.part"))


def test_the_same_name_twice_is_two_files(tmp_path):
    src = tmp_path / "out.txt"
    src.write_text("first\n")
    one = store(tmp_path / "state", src)
    src.write_text("second\n")
    two = store(tmp_path / "state", src)
    assert one["file_id"] != two["file_id"]
    assert find(tmp_path / "state", one["file_id"], "out.txt").read_text() == "first\n"


def test_store_refuses_a_directory_and_a_file_too_large(tmp_path, monkeypatch):
    with pytest.raises(FileError) as e:
        store(tmp_path / "state", tmp_path)
    assert e.value.code == "not_a_file"
    with pytest.raises(FileError) as e:
        store(tmp_path / "state", tmp_path / "missing.txt")
    assert e.value.code == "not_a_file"
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * 11)
    monkeypatch.setattr(files, "MAX_BYTES", 10)
    with pytest.raises(FileError) as e:
        store(tmp_path / "state", big)
    assert e.value.code == "too_large"


def test_find_takes_only_a_real_id_and_its_name(tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("a")
    rec = store(tmp_path / "state", src)
    state = tmp_path / "state"
    assert find(state, rec["file_id"], "b.txt") is None
    assert find(state, "nope", "a.txt") is None
    assert find(state, "..", "a.txt") is None
    assert find(state, f"{rec['file_id']}/..", "a.txt") is None


def test_the_url_quotes_the_name():
    assert url("x", "informe año #2.pdf") == "/files/x/informe%20a%C3%B1o%20%232.pdf"


def test_headers(tmp_path):
    def h(name, download=False):
        p = tmp_path / name
        p.write_bytes(b"x")
        return files.headers(p, download)

    png = h("a.png")
    assert png["X-Content-Type-Options"] == "nosniff"
    assert png["Referrer-Policy"] == "no-referrer"
    assert png["Cache-Control"] == "private, max-age=31536000, immutable"
    assert png["Content-Type"] == "image/png"
    assert "Content-Security-Policy" not in png and "Content-Disposition" not in png
    assert h("a.html")["Content-Security-Policy"] == "sandbox allow-scripts"
    assert h("a.svg")["Content-Security-Policy"] == "sandbox"
    assert h("a.xml")["Content-Security-Policy"] == "sandbox"
    assert "Content-Security-Policy" not in h("a.pdf")
    assert h("a.md")["Content-Type"] == "text/plain; charset=utf-8"
    assert h("a.zip")["Content-Disposition"].startswith("attachment")
    dl = h("informe año.pdf", download=True)
    assert (
        dl["Content-Disposition"]
        == "attachment; filename*=UTF-8''informe%20a%C3%B1o.pdf"
    )


def test_a_dotfile_is_served_like_any_other(tmp_path):
    src = tmp_path / ".aegis.yaml"
    src.write_text("agents: {}\n")
    rec = store(tmp_path / "state", src)
    assert find(tmp_path / "state", rec["file_id"], ".aegis.yaml").read_text() == (
        "agents: {}\n"
    )
