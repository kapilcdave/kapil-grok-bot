"""Render the final honest go/no-go report from pipeline outputs."""
import argparse
import json
from pathlib import Path


def load(path):
    with open(path) as handle:
        return json.load(handle)


def pct(value):
    return "n/a" if value is None else f"{100 * value:+.2f}%"


def number(value, digits=3):
    return "n/a" if value is None else f"{value:.{digits}f}"


def render(paths):
    birth = load(paths["birth"])
    same = load(paths["same"])
    deployer = load(paths["deployer"])
    latency = load(paths["latency"])
    funding_summary = load(paths["funding_summary"])
    funding = load(paths["funding"])
    compound0 = load(paths["compound0"])
    compound1 = load(paths["compound1"])
    scores = load(paths["scores"])
    lookup = load(paths["lookup"])

    bs = birth["summary"]
    same_ci = same["clustered_ci"]
    dp = deployer["primary"]
    fp = funding["funder_persistence"]
    fp_retail = funding["funder_persistence_at_retail_floor"]
    burst = funding["coordinated_funding_burst"]
    score_state = (
        "SHADOW-ALLOWLIST-EXPORTED"
        if scores["authorized"] else "FAIL-CLOSED-NO-ALLOWLIST"
    )
    capital = "NO-GO"

    lines = [
        "# RESULT — verified Pump.fun creator/funding graph",
        "",
        "Date: 2026-10-02",
        "",
        f"## Capital verdict: `{capital}`",
        "",
        ("The implemented bot remains capital-disabled. This report can authorize "
        "only a shadow allowlist; it cannot authorize transaction submission."),
        "",
        f"## Live shadow verdict: `{score_state}`",
        "",
        "## Birth-frame correction",
        "",
        f"- Audited inferred candidates: **{bs['n']:,}**",
        f"- Resolved candidates: **{bs['resolved']:,}**",
        f"- Earlier trades proving false birth: **{bs['false_births']:,}**",
        (f"- False-birth rate among resolved: "
        f"**{pct(bs['false_birth_rate_resolved'])}**"),
        f"- Authoritative creators decoded: **{bs['creators_decoded']:,}**",
        (f"- Creator equals first TradeEvent user: "
        f"**{pct(bs['creator_equals_first_trade_user_share'])}**"),
        "",
        "## Verified same-slot follower base rate",
        "",
        f"- Eligible verified launches: **{same['n']:,}**",
        f"- Mean return: **{pct(same['mean_return'])}**",
        (f"- Window-clustered 95% CI: "
        f"**[{pct(same_ci[0])}, {pct(same_ci[1])}]**"),
        f"- Median return: **{pct(same['median_return'])}**",
        f"- Win share: **{pct(same['share_positive'])}**",
        "",
        "## Direct creator history",
        "",
        f"- Verdict: **`{deployer['verdict']}`**",
        (f"- Top-history B launches: "
        f"**{(dp.get('top_b') or {}).get('n', 0):,}**"),
        (f"- Top-history B mean: "
        f"**{pct((dp.get('top_b') or {}).get('mean_roi'))}**"),
        (f"- Rank rho: "
        f"**{number(dp['rank_persistence'].get('rho'))}**"),
        (f"- Rank permutation p: "
        f"**{number(dp['rank_persistence'].get('one_sided_permutation_p'))}**"),
        (f"- Selection-null p: "
        f"**{number(dp['selection_null'].get('one_sided_p'))}**"),
        "",
        "## Entry-latency reachability",
        "",
        (f"- Verdict at the preregistered five-slot retail floor: "
        f"**`{latency['verdict']}`**"),
        f"- Magnitude verdict: **`{latency['magnitude_verdict']}`**",
        (f"- Funding graph justified by latency test: "
        f"**{latency['builds_funding_graph']}**"),
        "",
        "## Funding extraction",
        "",
        (f"- Verified births scanned: "
        f"**{funding_summary['summary']['n']:,}**"),
        (f"- Direct transfer edge coverage: "
        f"**{pct(funding_summary['summary']['direct_funder_rate'])}**"),
        (f"- High-confidence signer-source coverage: "
        f"**{pct(funding_summary['summary']['signer_source_rate'])}**"),
        (f"- Minimum qualifying direct transfer: "
        f"**{funding_summary['summary']['min_funding_lamports'] / 1e9:.3f} SOL**"),
        (f"- Dust sources skipped: "
        f"**{funding_summary['summary']['dust_sources_skipped']:,}**"),
        (f"- Repeated primary funders: "
        f"**{funding_summary['summary']['repeated_primary_funders']:,}**"),
        "",
        "## Funder-linked tests",
        "",
        (f"- Persistence verdict: "
        f"**`{funding['funder_persistence_verdict']}`**"),
        (f"- Top-funder B launches: "
        f"**{(fp.get('top_b') or {}).get('n', 0):,}**"),
        (f"- Top-funder B mean: "
        f"**{pct((fp.get('top_b') or {}).get('mean_roi'))}**"),
        (f"- Five-slot persistence verdict: "
        f"**`{funding['funder_persistence_at_retail_floor_verdict']}`**"),
        (f"- Five-slot top-funder B launches: "
        f"**{(fp_retail.get('top_b') or {}).get('n', 0):,}**"),
        (f"- Five-slot top-funder B mean: "
        f"**{pct((fp_retail.get('top_b') or {}).get('mean_roi'))}**"),
        (f"- Coordinated-burst flagged B launches: "
        f"**{(burst.get('flagged') or {}).get('n', 0):,}**"),
        (f"- Coordinated-burst useful exclusion: "
        f"**{burst['useful_exclusion']}**"),
        "",
        "## Graph-gated out-of-sample paper bankroll replay",
        "",
        "| assumed roundtrip tip | B trades | final SOL | return | max drawdown |",
        "|---:|---:|---:|---:|---:|",
        (f"| 0 SOL | {compound0['trades']:,} | "
        f"{compound0['final_sol']:.4f} | "
        f"{pct(compound0['return_fraction'])} | "
        f"{pct(compound0['max_drawdown_fraction'])} |"),
        (f"| 0.001 SOL | {compound1['trades']:,} | "
        f"{compound1['final_sol']:.4f} | "
        f"{pct(compound1['return_fraction'])} | "
        f"{pct(compound1['max_drawdown_fraction'])} |"),
        "",
        ("These replays enforce the same fail-closed graph authorization as the "
        "shadow exporter. A failed graph gate therefore produces zero trades, "
        "not a replay of the unfiltered launch population."),
        "",
        "## Live safety state",
        "",
        f"- Exported allow rows: **{scores['allow_rows']:,}**",
        f"- Score source: **{scores['source'] or 'none'}**",
        f"- Score key: **{scores.get('score_key') or 'none'}**",
        (f"- Requires live funding lookup: "
        f"**{scores.get('requires_live_funding_lookup', False)}**"),
        (f"- Funding lookup verdict: "
        f"**{scores.get('funding_lookup_verdict') or 'not-passed'}**"),
        f"- Live funding lookups sampled: **{lookup['n_births']:,}**",
        (f"- Completed inside feature deadline: "
        f"**{pct(lookup['completion_share'])}**"),
        "- Unknown, stale and same-slot-generated scores fail closed.",
        "- Capital enabled: **false**",
        "- Transaction build/sign/submit path in shadow gate: **absent**",
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default="RESULT-pumpfun-creator-funding-graph-20261002.md",
    )
    args = parser.parse_args()
    paths = {
        "birth": "data/pumpbirthaudit_summary.json",
        "same": "data/pumpsameentry_verified.json",
        "deployer": "data/pumpdeployer_verified.json",
        "latency": "data/pumpdeplat_verified.json",
        "funding_summary": "data/pumpfunding_summary.json",
        "funding": "data/pumpfundinganalyze.json",
        "compound0": "data/pumpcompound_zero_tip.json",
        "compound1": "data/pumpcompound_tip_001.json",
        "scores": "data/pumpgraph_scores_meta.json",
        "lookup": "data/pumpfundinglookup_live.json",
    }
    missing = [path for path in paths.values() if not Path(path).exists()]
    if missing:
        raise SystemExit("missing pipeline outputs: " + ", ".join(missing))
    text = render(paths)
    with open(args.output, "w") as handle:
        handle.write(text)
    print(args.output)


if __name__ == "__main__":
    main()
