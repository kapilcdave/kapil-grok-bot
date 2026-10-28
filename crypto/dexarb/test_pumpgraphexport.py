import json

import pumpgraphexport as E


def write_jsonl(path, rows):
    with open(path, "w") as handle:
        handle.writelines(json.dumps(row) + "\n" for row in rows)


def passing_latency():
    return {
        "verdict": "REACHABLE-WITHOUT-A-RACE",
        "magnitude_verdict": "SIZEABLE",
        "parameters": {"retail_floor_slots": 5},
        "sweep": {
            "5": {
                "selected_top_users": ["creator"],
                "top_b": {
                    "n": 50,
                    "windows": 6,
                    "mean_roi": 0.1,
                    "clustered_ci": [0.01, 0.2],
                },
                "top_vs_population_ci": [0.01, 0.2],
                "top_vs_bottom_ci": [0.01, 0.2],
                "rank_persistence": {
                    "rho": 0.2,
                    "one_sided_permutation_p": 0.01,
                },
                "observed_top_vs_population": 0.1,
                "selection_null": {"p95": 0.05},
            }
        },
    }


def test_failed_results_export_empty_allow_file(tmp_path):
    audit = tmp_path / "audit.jsonl"
    output = tmp_path / "scores.jsonl"
    metadata = tmp_path / "meta.json"
    write_jsonl(audit, [{
        "mint": "m",
        "first_sig": "s",
        "status": "create_in_first_trade_tx",
        "create_slot": 100,
    }])
    got = E.export_scores(
        output, metadata, audit,
        deployer_result={"verdict": "NO-DIRECT-DEPLOYER-PERSISTENCE"},
        latency_result=passing_latency(),
    )
    assert output.read_text() == ""
    assert got["authorized"] is False


def test_passed_verified_creator_result_exports_allow_rows(tmp_path):
    audit = tmp_path / "audit.jsonl"
    output = tmp_path / "scores.jsonl"
    metadata = tmp_path / "meta.json"
    write_jsonl(audit, [{
        "mint": "m",
        "first_sig": "s",
        "status": "create_in_first_trade_tx",
        "create_slot": 100,
    }])
    result = {
        "verdict": "DEPLOYER-HISTORY-PERSISTS",
        "birth_filter": {"enabled": True},
        "primary": {"selected_top_users": ["creator"]},
    }
    got = E.export_scores(
        output,
        metadata,
        audit,
        deployer_result=result,
        latency_result=passing_latency(),
    )
    row = json.loads(output.read_text())
    assert row["creator"] == "creator"
    assert row["as_of_slot"] == 100
    assert row["allow"] is True
    assert got["authorized"] is True


def test_latency_gated_result_blocks_passing_creator_signal(tmp_path):
    audit = tmp_path / "audit.jsonl"
    output = tmp_path / "scores.jsonl"
    metadata = tmp_path / "meta.json"
    write_jsonl(audit, [{
        "mint": "m",
        "first_sig": "s",
        "status": "create_in_first_trade_tx",
        "create_slot": 100,
    }])
    result = {
        "verdict": "DEPLOYER-HISTORY-PERSISTS",
        "birth_filter": {"enabled": True},
        "primary": {"selected_top_users": ["creator"]},
    }
    got = E.export_scores(
        output,
        metadata,
        audit,
        deployer_result=result,
        latency_result={
            "verdict": "LATENCY-GATED",
            "magnitude_verdict": "TOO-SMALL-TO-TRADE",
        },
    )
    assert output.read_text() == ""
    assert got["authorized"] is False


def test_passed_funder_result_exports_funders_not_stale_creator_mapping(
    tmp_path,
):
    audit = tmp_path / "audit.jsonl"
    output = tmp_path / "scores.jsonl"
    metadata = tmp_path / "meta.json"
    write_jsonl(audit, [{
        "mint": "m",
        "first_sig": "s",
        "status": "create_in_first_trade_tx",
        "create_slot": 100,
    }])
    funding_result = {
        "funder_persistence_at_retail_floor_verdict":
            "FUNDER-HISTORY-PERSISTS",
        "funder_persistence_at_retail_floor": {
            "selected_top_users": ["funder"]
        },
    }
    got = E.export_scores(
        output,
        metadata,
        audit,
        funding_result=funding_result,
        latency_result=passing_latency(),
        funding_lookup_result={"verdict": "LOOKUP-REACHABLE"},
    )
    row = json.loads(output.read_text())
    assert row["identity_type"] == "funder"
    assert row["funder"] == "funder"
    assert "creator" not in row
    assert got["requires_live_funding_lookup"] is True


def test_funder_signal_fails_closed_without_live_lookup_gate(tmp_path):
    audit = tmp_path / "audit.jsonl"
    output = tmp_path / "scores.jsonl"
    metadata = tmp_path / "meta.json"
    write_jsonl(audit, [{
        "mint": "m",
        "first_sig": "s",
        "status": "create_in_first_trade_tx",
        "create_slot": 100,
    }])
    got = E.export_scores(
        output,
        metadata,
        audit,
        funding_result={
            "funder_persistence_at_retail_floor_verdict":
                "FUNDER-HISTORY-PERSISTS",
            "funder_persistence_at_retail_floor": {
                "selected_top_users": ["funder"]
            },
        },
        latency_result=passing_latency(),
    )
    assert got["authorized"] is False
    assert output.read_text() == ""


def test_creator_result_cannot_use_weaker_latency_only_gate(tmp_path):
    audit = tmp_path / "audit.jsonl"
    output = tmp_path / "scores.jsonl"
    metadata = tmp_path / "meta.json"
    write_jsonl(audit, [{
        "mint": "m",
        "first_sig": "s",
        "status": "create_in_first_trade_tx",
        "create_slot": 100,
    }])
    deployer = {
        "verdict": "DEPLOYER-HISTORY-PERSISTS",
        "birth_filter": {"enabled": True},
        "primary": {"selected_top_users": ["creator"]},
    }
    got = E.export_scores(
        output,
        metadata,
        audit,
        deployer_result=deployer,
        latency_result={
            "verdict": "REACHABLE-WITHOUT-A-RACE",
            "magnitude_verdict": "SIZEABLE",
            "sweep": {"5": {"selected_top_users": ["creator"]}},
        },
    )
    assert got["authorized"] is False
