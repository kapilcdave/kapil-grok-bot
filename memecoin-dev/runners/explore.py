"""Which launch, dev, story and flow features separate the coins that held from the ones that rugged.
EXPLORATION ONLY: one day, in sample, many features looked at. It proposes the next preregistered
rule; it does not score one.

    python -m runners.explore FEATURES.jsonl 'tape_a/runners_*.jsonl*' ['tape_b/...']

Outcome per coin, from the bot's would-be entry (first snapshot within 3 min of enrichment), net of
the same costs as the scorer: `2h` net return (the horizon both tapes cover), `rug` = worth <= 20% of
entry at 2 h or delisted, `2x` = touched 2x entry within 4 h.
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict

from . import rule, score

H = 2


def outcome(c: dict) -> dict | None:
    t = score.trade(c, H, 100, 0.01)
    if t is None:
        return None
    t0 = c["enrich"]["ts"]
    e = next(s for s in c["snaps"] if t0 <= s["ts"] <= t0 + score.ENTRY_WINDOW_S)
    win = [s["px"] for s in c["snaps"] if e["ts"] <= s["ts"] <= e["ts"] + 4 * 3600]
    return {"net": t["net"], "rug": t["gone"] or t["gross"] <= -0.8, "x2": max(win) >= 2 * e["px"]}


def bins(c: dict, f: dict) -> dict[str, str]:
    a, e = c["admit"], c["enrich"]
    lc, cd, dv, po, tx, kd = (f.get(k) or {} for k in ("launch", "candles", "dev", "post", "text", "kind"))
    out: dict[str, str] = {}

    def cut(v, edges, labels):
        if v is None:
            return "?"
        for edge, lab in zip(edges, labels):
            if v < edge:
                return lab
        return labels[-1]
    g = lc.get("grad_lag_s")
    out["grad_lag"] = "not graduated" if lc and lc.get("graduated") is None and not lc.get("err") else \
        cut(g, (1, 600, 3600), ("0s (curve bought at launch)", "<10 min", "10-60 min", ">1 h"))
    out["launchpad"] = str(lc.get("launchpad"))
    sup = lc.get("supply") or 1e9
    out["dev_buy_sol"] = cut(dv.get("dev_buy_sol"), (0.5, 3, 10, 1e9), ("<0.5", "0.5-3", "3-10", ">=10"))
    out["dev_buy_pct"] = cut((dv.get("dev_buy_tokens") or 0) / sup if "dev_buy_tokens" in dv else None,
                             (0.02, 0.1, 0.3, 2), ("<2%", "2-10%", "10-30%", ">=30%"))
    out["dev_moved_or_sold_10m"] = "?" if "dev_moved_10m" not in dv else \
        ("yes" if (dv["dev_moved_10m"] + dv["dev_sold_10m"]) > 0 else "no")
    out["dev_wallet_age"] = cut(dv.get("dev_funded_s_before"), (600, 6 * 3600, 1e12), ("<10 min (burner)", "10 min-6 h", ">6 h"))
    out["dev_mints"] = cut(lc.get("dev_mints"), (2, 11, 1e9), ("1", "2-10", ">10"))
    out["mc_first_candle_hi"] = cut(cd.get("mc_first_hi") or None, (30e3, 100e3, 400e3, 1e12),
                                    ("<30k", "30-100k", "100-400k", ">=400k"))
    out["mc_at_5m"] = cut(cd.get("mc_5m") or None, (50e3, 150e3, 1e12), ("<50k", "50-150k", ">=150k"))
    created = lc.get("created")
    out["age_at_admit"] = cut((a["ts"] - created) / 3600 if created else None, (1, 6, 24, 1e9), ("<1h", "1-6h", "6-24h", ">=24h"))
    pt = po.get("post_ts")
    out["x_post"] = "none" if not pt else ("coin after post" if created and created >= pt - 300 else "coin BEFORE post")
    out["kind"] = str(kd.get("kind"))
    for q in ("post_names_coin", "post_by_subject", "mainstream"):
        out[q] = cut(kd.get(q), (0.5, 2), ("<0.5", ">=0.5"))
    out["polymarket"], out["charity"] = str(tx.get("polymarket")), str(tx.get("charity"))
    out["telegram"], out["website"] = str(tx.get("has_tg")), str(tx.get("has_site"))
    out["session"] = score.session(a.get("hour_utc", 0))
    out["v1_rule"] = rule.why_not(a, e) or "PICK"
    top10 = (e.get("holders") or {}).get("top10")
    out["top10_at_admit"] = cut(top10, (0.3, 0.6, 2), ("<30%", "30-60%", ">=60%"))
    return out


def main() -> None:
    feats = {}
    for l in open(sys.argv[1]):
        if l.strip():
            r = json.loads(l)
            feats[r["mint"]] = r
    coins: dict[str, dict] = {}
    for pat in sys.argv[2:]:
        coins.update(score.load(pat))
    by: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    n = 0
    for m, c in coins.items():
        o = outcome(c)
        if o is None or m not in feats:
            continue
        n += 1
        for k, v in bins(c, feats[m]).items():
            by[k][v].append(o)
            by["ALL"]["all"].append(o) if k == "launchpad" else None
    print(f"{n} coins with features and a {H} h outcome (net of costs). In sample, one day: hypotheses only.\n")
    print(f"{'feature':<24}{'value':<30}{'n':>4} {'mean':>7} {'median':>7} {'rug':>5} {'2x':>5}")
    for k in ["ALL"] + [k for k in by if k != "ALL"]:
        for v, os_ in sorted(by[k].items(), key=lambda kv: -len(kv[1])):
            net = [o["net"] for o in os_]
            print(f"{k:<24}{v[:29]:<30}{len(os_):>4} {statistics.fmean(net):+7.0%} {statistics.median(net):+7.0%} "
                  f"{sum(o['rug'] for o in os_) / len(os_):5.0%} {sum(o['x2'] for o in os_) / len(os_):5.0%}")
        print()


if __name__ == "__main__":
    main()
