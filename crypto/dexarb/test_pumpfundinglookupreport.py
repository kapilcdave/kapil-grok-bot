import pumpfundinglookupreport as R


def row(seconds=1.0, decision="reject_unknown_funder"):
    return {
        "funding_lookup_seconds": seconds,
        "funding_lookup_deadline_seconds": 2.0,
        "decision": decision,
    }


def test_lookup_gate_requires_power_and_reliability():
    small = R.summarize([row()] * 99)
    assert small["gates"] == {"power": False, "reliability": True}
    assert small["verdict"] == "LOOKUP-NOT-REACHABLE"

    reliable = R.summarize([row()] * 95 + [
        row(2.1, "reject_funding_lookup_deadline")
    ] * 5)
    assert reliable["completion_share"] == 0.95
    assert reliable["verdict"] == "LOOKUP-REACHABLE"


def test_lookup_errors_do_not_count_as_completed():
    got = R.summarize(
        [row()] * 94 + [row(0.1, "reject_funding_lookup_error")] * 6
    )
    assert got["completion_share"] == 0.94
    assert got["verdict"] == "LOOKUP-NOT-REACHABLE"
