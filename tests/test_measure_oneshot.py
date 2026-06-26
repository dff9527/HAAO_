from scripts.measure_oneshot import _classify_trial


def test_diff_pending_with_no_failures_is_one_shot() -> None:
    assert _classify_trial("diff_pending", 0) == "one_shot"


def test_diff_pending_after_failure_is_retry_then_pass() -> None:
    assert _classify_trial("diff_pending", 1) == "retry_then_pass"


def test_blocked_ticket_is_not_a_local_finish() -> None:
    assert _classify_trial("blocked", 4) == "blocked"


def test_later_review_states_remain_local_finishes() -> None:
    for status in ("review", "awaiting_acceptance", "done"):
        assert _classify_trial(status, 0) == "one_shot"
