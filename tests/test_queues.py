def test_queue_items():
    from aegis.notice_sources import Item
    from aegis.queues import queue_items

    assert queue_items([], 0) == []
    assert queue_items(["review-a", "review-b"], 1) == [
        Item(
            "out", "info", "queue tasks out: 2 running (review-a, review-b), 1 waiting"
        )
    ]
    assert queue_items([], 2) == [Item("out", "info", "queue tasks out: 2 waiting")]
