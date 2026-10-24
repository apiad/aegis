"""Skills in the store: a folder's files, and skills in the hint."""

import pytest

from aegis.knowledge import hint
from aegis.knowledge.service import Knowledge
from aegis.ops import OpError

from .knowledge_samples import mem, skill


@pytest.fixture
def kn(tmp_path):
    k = Knowledge(tmp_path)
    yield k
    k.usage.stop()


async def test_a_skill_folder_lists_and_reads_its_files(kn):
    await kn.store.save(
        "ana",
        skill("release", "Cut a release"),
        "t",
        extra={
            "bump.py": b"print(1)\n",
            "lib/util.py": b"X = 1\n",
            "logo.bin": b"\0\1",
        },
    )
    assert kn.store.files("ana", "release") == [
        "SKILL.md",
        "bump.py",
        "lib/util.py",
        "logo.bin",
    ]
    assert kn.store.read_file("ana", "release", "lib/util.py") == b"X = 1\n"
    assert kn.store.read_file("ana", "release", "logo.bin") == b"\0\1"
    assert kn.store.read_file("ana", "release", "SKILL.md").startswith(b"---\n")
    with pytest.raises(OpError) as e:
        kn.store.read_file("ana", "release", "nope.py")
    assert e.value.code == "not_found"
    with pytest.raises(OpError) as e:
        kn.store.files("ana", "nothing")
    assert e.value.code == "not_found"


async def test_read_file_refuses_dot_dot(kn, tmp_path):
    await kn.store.save("ana", skill("release"), "t")
    await kn.store.save("ana", mem("secret", "a fact"), "t")
    outside = tmp_path / "outside.txt"
    outside.write_text("no")
    folder = kn.store.dir("ana") / "skills" / "release"
    (folder / "link").symlink_to(outside)
    for rel in (
        "../../memories/secret.md",
        "/etc/passwd",
        "..",
        "a/../../x",
        "link",
        "",
    ):
        with pytest.raises(OpError) as e:
            kn.store.read_file("ana", "release", rel)
        assert e.value.code == "bad_params", rel
    assert "link" not in kn.store.files("ana", "release")


async def test_the_hint_names_skills_and_memories_separately(kn):
    await kn.store.save("ana", skill("deploy-bot", "Deploy the bot to its host"), "t")
    await kn.store.save("ana", mem("bot-host", "The bot runs on host b"), "t")
    b = kn.hint_for("ana", "s1", "deploy the bot", None)
    lines = [ln for ln in b.splitlines() if ln]
    assert lines[0] == hint.HEADER
    assert lines[1] == "skills: deploy-bot: Deploy the bot to its host"
    assert lines[2] == "memories: bot-host: The bot runs on host b"
    assert lines[3].startswith("read with skill_read / memory_read")
    rows = kn.usage.rows("ana", "deploy-bot")
    assert [(r["kind"], r["event"], r["detail"]) for r in rows] == [
        ("skill", "hinted", "rank=1")
    ]
    # A proposed skill is never hinted.
    await kn.store.save(
        "ana", skill("deploy-bot", "Deploy the bot", status="proposed"), "t"
    )
    await kn.refresh("ana")
    assert "deploy-bot" not in kn.hint_for("ana", "s1", "deploy the bot", None)
