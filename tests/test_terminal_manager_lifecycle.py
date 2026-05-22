import pytest
from pathlib import Path
from aegis.terminal.manager import TerminalManager, TerminalAlreadyExists


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    return tmp_path / "aegis" / "state" / "terminals"


async def test_spawn_creates_state_dir_and_meta(state_dir):
    mgr = TerminalManager(state_dir=state_dir)
    info = await mgr.spawn(name="build", shell="/bin/bash", cwd=str(state_dir.parent))
    assert info.name == "build"
    assert info.shell == "/bin/bash"
    assert info.pid > 0
    assert (state_dir / "build" / "meta.json").exists()
    await mgr.close("build")


async def test_spawn_duplicate_name_errors(state_dir):
    mgr = TerminalManager(state_dir=state_dir)
    await mgr.spawn(name="dup", shell="/bin/bash")
    with pytest.raises(TerminalAlreadyExists):
        await mgr.spawn(name="dup", shell="/bin/bash")
    await mgr.close("dup")


async def test_list_returns_spawned_terminals(state_dir):
    mgr = TerminalManager(state_dir=state_dir)
    await mgr.spawn(name="a", shell="/bin/bash")
    await mgr.spawn(name="b", shell="/bin/bash")
    names = {t.name for t in mgr.list()}
    assert names == {"a", "b"}
    await mgr.close("a")
    await mgr.close("b")


async def test_close_removes_from_list(state_dir):
    mgr = TerminalManager(state_dir=state_dir)
    await mgr.spawn(name="gone", shell="/bin/bash")
    await mgr.close("gone")
    assert all(t.name != "gone" for t in mgr.list())


async def test_close_preserves_ledger_by_default(state_dir):
    mgr = TerminalManager(state_dir=state_dir)
    await mgr.spawn(name="kept", shell="/bin/bash")
    await mgr.close("kept")
    assert (state_dir / "kept" / "meta.json").exists()
