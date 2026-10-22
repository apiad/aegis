"""The rename nudge (#586): a session remembers whether its handle and its title
were set, and turn_end reminds an agent until both are."""

from .test_agents import world  # noqa: F401


async def test_a_new_session_has_neither_name_set(world):  # noqa: F811
    s = await world.spawn()
    assert (s.handle_set, s.title_set) == (False, False)
    assert s.meta()["handle_set"] is False


async def test_rename_with_a_handle_sets_handle_set_only(world):  # noqa: F811
    s = await world.spawn()
    world.app.sessions.rename(s.log_id, "tab-bar", None)
    assert (s.handle_set, s.title_set) == (True, False)
    assert s.meta()["handle_set"] is True


async def test_a_meta_from_before_the_field_reads_as_set(world):  # noqa: F811
    s = await world.spawn()
    old = s.meta()
    del old["handle_set"]
    assert world.app.sessions._session(old).handle_set is True
