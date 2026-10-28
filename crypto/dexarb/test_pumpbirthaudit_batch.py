import pumpbirthaudit_batch as A


def test_empty_result_has_resume_key_and_unresolved_status():
    candidate = {
        "window": 1,
        "mint": "mint",
        "first_sig": "sig",
        "first_slot": 10,
        "state_user": "user",
        "offset_slots": 5,
    }
    got = A.empty_result(candidate)
    assert got["mint"] == "mint"
    assert got["first_sig"] == "sig"
    assert got["status"] == "unresolved"


def test_transaction_call_is_finalized_json():
    method, params = A.transaction_call("sig")
    assert method == "getTransaction"
    assert params[0] == "sig"
    assert params[1]["encoding"] == "json"
    assert params[1]["commitment"] == "finalized"


def test_rpc_429_is_retryable_but_other_errors_are_returned():
    assert A.retryable_rpc_error(
        {"code": 429, "message": "Too many requests"}
    )
    assert A.retryable_rpc_error(
        {"code": -32000, "message": "rate limit exceeded"}
    )
    assert not A.retryable_rpc_error(
        {"code": -32602, "message": "Invalid params"}
    )


def test_needs_older_marker_is_not_a_resolved_birth():
    candidate = {
        "window": 1,
        "mint": "mint",
        "first_sig": "sig",
        "first_slot": 10,
        "state_user": "user",
        "offset_slots": 5,
    }
    marker = A.empty_result(candidate)
    marker["status"] = "needs_older_scan"
    assert marker["error"] is None
    assert marker["create_sig"] is None
