def test_barrel_exports_slice1_surface():
    from aegis.groups import (
        BroadcastInFlight,
        BroadcastRecord,
        BroadcastTracker,
        Group,
        GroupExists,
        GroupRegistry,
        GroupResult,
        GroupRuntime,
        MemberRef,
        MemberResult,
        UnknownGroup,
        concat,
        get_reducer,
        register_reducer,
    )
    assert all([
        BroadcastInFlight, BroadcastRecord, BroadcastTracker, Group,
        GroupExists, GroupRegistry, GroupResult, GroupRuntime, MemberRef,
        MemberResult, UnknownGroup, concat, get_reducer, register_reducer,
    ])
