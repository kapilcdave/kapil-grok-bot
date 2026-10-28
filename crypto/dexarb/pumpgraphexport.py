"""Export live shadow scores only from a passed, birth-verified result.

There is intentionally no force flag. A failed or incomplete research gate
produces an empty allow file, so the live shadow gate rejects every creator.
"""
import argparse
import json
from pathlib import Path

import pumpdeployer as D


def read_json(path):
    with open(path) as handle:
        return json.load(handle)


def latest_audit(path):
    return D.load_birth_audit(path)


def full_persistence_gate(stratum):
    if not stratum:
        return False
    top = stratum.get("top_b")
    top_pop = stratum.get("top_vs_population_ci")
    top_bottom = stratum.get("top_vs_bottom_ci")
    rank = stratum.get("rank_persistence") or {}
    null = stratum.get("selection_null") or {}
    return bool(
        top
        and top.get("n", 0) >= 50
        and top.get("windows", 0) >= 6
        and top.get("mean_roi", 0) > 0
        and top.get("clustered_ci")
        and top["clustered_ci"][0] > 0
        and top_pop
        and top_pop[0] > 0
        and top_bottom
        and top_bottom[0] > 0
        and rank.get("rho") is not None
        and rank["rho"] > 0
        and rank.get("one_sided_permutation_p", 1) < 0.05
        and stratum.get("observed_top_vs_population") is not None
        and null.get("p95") is not None
        and stratum["observed_top_vs_population"] > null["p95"]
    )


def authorized_source(
    deployer_result,
    funding_result,
    latency_result,
    funding_lookup_result=None,
):
    if not (
        latency_result
        and latency_result.get("verdict") == "REACHABLE-WITHOUT-A-RACE"
        and latency_result.get("magnitude_verdict") == "SIZEABLE"
    ):
        return None, set()
    if funding_result and (
        funding_result.get("funder_persistence_at_retail_floor_verdict")
        == "FUNDER-HISTORY-PERSISTS"
        and funding_lookup_result
        and funding_lookup_result.get("verdict") == "LOOKUP-REACHABLE"
    ):
        users = funding_result["funder_persistence_at_retail_floor"].get(
            "selected_top_users", []
        )
        return "funder", set(users)
    if (
        deployer_result
        and deployer_result.get("verdict") == "DEPLOYER-HISTORY-PERSISTS"
        and deployer_result.get("birth_filter", {}).get("enabled") is True
    ):
        floor_slots = str(
            latency_result.get("parameters", {}).get(
                "retail_floor_slots", 5
            )
        )
        floor = latency_result.get("sweep", {}).get(floor_slots)
        if full_persistence_gate(floor):
            users = floor.get("selected_top_users", [])
            return "creator", set(users)
    return None, set()


def export_scores(
    output_path,
    metadata_path,
    birth_audit_path,
    deployer_result=None,
    funding_result=None,
    latency_result=None,
    funding_lookup_result=None,
    funding_path=None,
):
    audit = latest_audit(birth_audit_path)
    verified = [
        row for row in audit.values()
        if row["status"] in D.VERIFIED_BIRTH
    ]
    as_of_slot = max((row["create_slot"] for row in verified), default=None)
    source, allowed = authorized_source(
        deployer_result,
        funding_result,
        latency_result,
        funding_lookup_result,
    )
    scores = []
    if source == "creator" and as_of_slot is not None:
        for creator in sorted(allowed):
            scores.append({
                "identity_type": "creator",
                "identity": creator,
                "creator": creator,
                "as_of_slot": as_of_slot,
                "allow": True,
                "reason": "passed_direct_creator_persistence_gate",
                "model": "pumpfun-direct-creator-v1",
            })
    elif source == "funder" and as_of_slot is not None:
        # Do not turn historical creator->funder edges into a creator
        # allowlist. That would lose the wallet-rotation signal and would
        # incorrectly assume a creator's old funder is still its current one.
        # The live read-only enricher must resolve the new creator's own
        # pre-creation funding edge before joining this score.
        for funder in sorted(allowed):
            scores.append({
                "identity_type": "funder",
                "identity": funder,
                "funder": funder,
                "as_of_slot": as_of_slot,
                "allow": True,
                "reason": "passed_signer_funder_persistence_gate",
                "model": "pumpfun-signer-funder-v1",
            })

    with open(output_path, "w") as handle:
        handle.writelines(
            json.dumps(row, sort_keys=True) + "\n" for row in scores
        )
    metadata = {
        "authorized": bool(scores),
        "source": source,
        "score_key": source,
        "requires_live_funding_lookup": source == "funder",
        "funding_lookup_verdict": (
            (funding_lookup_result or {}).get("verdict")
        ),
        "allow_rows": len(scores),
        "as_of_slot": as_of_slot,
        "verified_births": len(verified),
        "fail_closed_reason": (
            None if scores else "no_birth_verified_research_gate_passed"
        ),
    }
    with open(metadata_path, "w") as handle:
        json.dump(metadata, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return metadata


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--birth-audit", default="data/pumpbirthaudit.jsonl")
    parser.add_argument("--deployer-result", default="data/pumpdeployer_verified.json")
    parser.add_argument("--funding-result", default="data/pumpfundinganalyze.json")
    parser.add_argument("--latency-result", default="data/pumpdeplat_verified.json")
    parser.add_argument(
        "--funding-lookup-result",
        default="data/pumpfundinglookup_live.json",
    )
    parser.add_argument("--funding", default="data/pumpfunding.jsonl")
    parser.add_argument("--output", default="data/pumpgraph_scores.jsonl")
    parser.add_argument("--metadata", default="data/pumpgraph_scores_meta.json")
    args = parser.parse_args()

    deployer = (
        read_json(args.deployer_result)
        if Path(args.deployer_result).exists() else None
    )
    funding_result = (
        read_json(args.funding_result)
        if Path(args.funding_result).exists() else None
    )
    latency_result = (
        read_json(args.latency_result)
        if Path(args.latency_result).exists() else None
    )
    funding_lookup_result = (
        read_json(args.funding_lookup_result)
        if Path(args.funding_lookup_result).exists() else None
    )
    metadata = export_scores(
        args.output,
        args.metadata,
        args.birth_audit,
        deployer_result=deployer,
        funding_result=funding_result,
        latency_result=latency_result,
        funding_lookup_result=funding_lookup_result,
        funding_path=args.funding,
    )
    print(json.dumps(metadata, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
