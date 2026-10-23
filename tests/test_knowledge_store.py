import asyncio

import pytest

from aegis.knowledge import history
from aegis.knowledge.store import Store
from aegis.ops import OpError

from .knowledge_samples import mem, skill


async def test_names_are_unique_across_kinds(tmp_path):
    st = Store(tmp_path)
    await st.save("ana", mem("release"), "s1")
    with pytest.raises(OpError) as e:
        await st.save("ana", skill("release"), "s1")
    assert e.value.code == "name_taken"
    # Another principal is another namespace.
    await st.save("ben", skill("release"), "s2")


async def test_a_malformed_file_is_skipped_and_the_rest_work(tmp_path, caplog):
    st = Store(tmp_path)
    await st.save("ana", mem("good"), "s1")
    (tmp_path / "ana" / "memories" / "bad.md").write_text("---\nname: [x\n---\n")
    assert [i.name for i in st.items("ana")] == ["good"]
    assert "bad.md" in caplog.text


async def test_concurrent_saves_both_land(tmp_path):
    st = Store(tmp_path)
    await asyncio.gather(st.save("ana", mem("a"), "s1"), st.save("ana", mem("b"), "s2"))
    assert {i.name for i in st.items("ana")} == {"a", "b"}
    assert len(history.log(tmp_path / "ana", "memories")) == 2


async def test_principal_names_cannot_escape(tmp_path):
    with pytest.raises(OpError):
        Store(tmp_path).items("../etc")


async def test_a_bad_item_name_writes_nothing(tmp_path):
    st = Store(tmp_path)
    with pytest.raises(OpError) as e:
        await st.save("ana", mem("../x"), "s1")
    assert e.value.code == "bad_name"
    assert not (tmp_path / "ana" / "memories").exists()


async def test_a_skill_saves_its_extra_files_and_deletes_whole(tmp_path):
    st = Store(tmp_path)
    await st.save("ana", skill("release"), "s1", extra={"bump.py": b"print(1)\n"})
    assert (tmp_path / "ana" / "skills" / "release" / "bump.py").read_bytes()
    assert st.get("ana", "release").kind == "skill"
    v = st.version("ana")
    sha = await st.delete("ana", "release", "s1")
    assert sha and st.get("ana", "release") is None and st.version("ana") > v
    with pytest.raises(OpError) as e:
        await st.delete("ana", "release", "s1")
    assert e.value.code == "not_found"


async def test_extra_files_cannot_escape_the_skill(tmp_path):
    with pytest.raises(OpError):
        await Store(tmp_path).save("ana", skill("x"), "s1", extra={"../../a": b""})
