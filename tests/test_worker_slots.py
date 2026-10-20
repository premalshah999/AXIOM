from jobpilot.pipeline_comparison import NORMAL, SPECIALIST, pool_slot_ids


def test_eight_keeps_six_normal_and_two_specialists():
    assert pool_slot_ids(NORMAL, 8) == [1, 2, 3, 4, 5, 6]
    assert pool_slot_ids(SPECIALIST, 8) == [7, 8]


def test_larger_fleet_keeps_two_specialists_last():
    assert pool_slot_ids(NORMAL, 20) == list(range(1, 19))
    assert pool_slot_ids(SPECIALIST, 20) == [19, 20]
    assert len(pool_slot_ids(NORMAL, 99)) == 30


def test_snapshot_lanes_match_the_allocator(service):
    """The Batch view labels each slot from this snapshot; 12 workers are 10 regular + 2 specialist."""
    from jobpilot.pipeline_comparison import snapshot

    for limit in (8, 12):
        view = snapshot(service.db, limit)
        specialists = [w["slot"] for w in view["assignments"] if w["lane"] == SPECIALIST]
        assert specialists == pool_slot_ids(SPECIALIST, limit)
        assert len(specialists) == view["specialist_workers"]
        assert len(view["assignments"]) - len(specialists) == view["normal_workers"]
