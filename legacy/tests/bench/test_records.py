from aegis.bench.records import MarkerSeq, Recorder, read_jsonl


def test_recorder_appends_and_read_skips_damage(tmp_path):
    p = tmp_path / "x.jsonl"
    r = Recorder(p)
    r.write({"k": "a", "v": 1})
    r.close()
    with p.open("a") as fh:
        fh.write("{not json\n")
    Recorder(p).write({"k": "b"})
    assert [x["k"] for x in read_jsonl(p)] == ["a", "b"]


def test_read_missing_is_empty(tmp_path):
    assert read_jsonl(tmp_path / "nope.jsonl") == []


def test_marker_seq_is_shared_across_instances(tmp_path):
    a, b = MarkerSeq(tmp_path / "seq"), MarkerSeq(tmp_path / "seq")
    assert [a.next(), b.next(), a.next()] == [1, 2, 3]
