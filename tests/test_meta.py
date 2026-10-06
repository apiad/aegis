import json
from pathlib import Path

from aegis.meta import MetaStore, rebuild
from aegis.transcript.store import Store


def meta(log_id="a", **kw):
    return {"log_id": log_id, "handle": "quiet-owl", "archived": False, **kw}


def test_write_is_atomic_and_readable(tmp_path: Path):
    ms = MetaStore(tmp_path / "sessions")
    ms.write(meta(title="t"))
    assert [m["title"] for m in ms.read_all()[0]] == ["t"]
    assert not list((tmp_path / "sessions").glob("*.tmp"))


def test_throttled_writes_coalesce_until_flush(tmp_path: Path):
    ms = MetaStore(tmp_path)
    ms.write_soon("a", lambda: meta(n=1))  # first write goes through at once
    ms.write_soon("a", lambda: meta(n=2))
    ms.write_soon("a", lambda: meta(n=3))
    assert json.loads(ms.path("a").read_text())["n"] == 1
    ms.flush()
    assert json.loads(ms.path("a").read_text())["n"] == 3


def test_damaged_meta_is_reported_not_raised(tmp_path: Path):
    ms = MetaStore(tmp_path)
    ms.write(meta("good"))
    (tmp_path / "bad.json").write_text("{nope")
    (tmp_path / "liar.json").write_text(json.dumps(meta("other")))
    metas, broken = ms.read_all()
    assert [m["log_id"] for m in metas] == ["good"]
    assert sorted(p.name for p in broken) == ["bad.json", "liar.json"]


def test_rebuild_recovers_spec_claude_id_and_title(tmp_path: Path):
    path = tmp_path / "20261006-x.jsonl"
    s = Store(path)
    s.append(
        {
            "src": "aegis",
            "kind": "spawn",
            "ts": 1.0,
            "profile": "opus",
            "model": "opus",
            "effort": "high",
            "permission": "full",
            "cwd": "/w",
        }
    )
    s.append(
        {
            "src": "aegis",
            "kind": "send",
            "ts": 2.0,
            "text": "fix the flaky test\nplease",
        }
    )
    s.append(
        {
            "src": "claude",
            "ts": 3.0,
            "line": json.dumps(
                {"type": "system", "subtype": "init", "session_id": "cs-1"}
            ),
        }
    )
    s.close()
    m = rebuild(path)
    assert m["log_id"] == "20261006-x" and m["archived"] is True
    assert (m["profile"], m["cwd"], m["claude_session_id"], m["title"]) == (
        "opus",
        "/w",
        "cs-1",
        "fix the flaky test",
    )
    assert (m["created_at"], m["last_activity"]) == (1.0, 3.0)


def test_rebuild_gives_up_without_a_spawn_record(tmp_path: Path):
    p = tmp_path / "x.jsonl"
    p.write_text("garbage\n")
    assert rebuild(p) is None
