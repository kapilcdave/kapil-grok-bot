"""Score the runner tape: did the picks pay after costs, and did they beat the coins passed on?

    python -m runners.score 'data/runners_*.jsonl*' [--clip-usd 100] [--fee 0.01]

For each admitted coin the decision time is its `enrich` row (when the bot could first have acted).
Entry is the first snapshot within 3 min after that; exit is the first snapshot at least H after entry
(within 15 min, otherwise censored). A coin whose pair vanished (`gone`) before the exit counts as -100%.

Cost per leg = fee + price impact of a `clip-usd` order against half the pool's USD liquidity, so a
thin pool costs more. Groups, all inside the same $150k-$5M admission band so they are comparable:

    primary    rule.why_not == ''                            (the preregistered rule)
    no_story   original + band, story ignored                (primary minus this = what the story adds)
    jev        rule.jev_rule                                  (only with a TYPESAFE_API_KEY on the box)
    vamps      band, but not the biggest-and-earliest of its look-alikes
    control    band, failed the primary for any reason

`max` columns are the best price inside the hold, which nobody gets; they describe how often runners
happen, they are not P&L. Read the preregistration before reading these numbers.
"""
from __future__ import annotations

import argparse
import glob
import gzip
import json
import random
import statistics
from collections import defaultdict

from . import exits, rule

HOLDS_H = (1, 4, 12, 24)
PRIMARY_H = 4
ENTRY_WINDOW_S, EXIT_SLACK_S = 180, 900


def rows(pattern: str):
    for path in sorted(glob.glob(pattern)):
        opener = gzip.open if path.endswith(".gz") else open
        try:
            with opener(path, "rt") as fh:
                for line in fh:
                    try:
                        yield json.loads(line)
                    except ValueError:
                        continue     # a torn last line from a killed writer
        except FileNotFoundError:
            continue


def load(pattern: str) -> dict[str, dict]:
    coins: dict[str, dict] = defaultdict(lambda: {"snaps": [], "gone": []})
    for r in rows(pattern):
        c = coins[r.get("mint", "")]
        if r["t"] == "admit":
            c.setdefault("admit", r)
        elif r["t"] == "enrich":
            c.setdefault("enrich", r)
        elif r["t"] == "snap" and r.get("px"):
            c["snaps"].append(r)
        elif r["t"] == "gone":
            c["gone"].append(r["ts"])
    for c in coins.values():
        c["snaps"].sort(key=lambda s: s["ts"])
    return {m: c for m, c in coins.items() if "admit" in c and "enrich" in c}


def load_news(pattern: str) -> dict[str, dict]:
    """News-first candidates: `nadmit` / `nenrich` rows, priced by the same snapshots."""
    coins: dict[str, dict] = defaultdict(lambda: {"snaps": [], "gone": []})
    for r in rows(pattern):
        c = coins[r.get("mint", "")]
        if r["t"] == "nadmit":
            c.setdefault("admit", r)
        elif r["t"] == "nenrich":
            c.setdefault("enrich", r)
        elif r["t"] == "snap" and r.get("px"):
            c["snaps"].append(r)
        elif r["t"] == "gone":
            c["gone"].append(r["ts"])
    for c in coins.values():
        c["snaps"].sort(key=lambda s: s["ts"])
    return {m: c for m, c in coins.items() if "admit" in c and "enrich" in c}


def news_groups(admit: dict, enr: dict) -> list[str]:
    if rule.news_why_not(admit, enr) == "mc":
        return []
    out = ["news_all"]
    out.append("news_pick" if not rule.news_why_not(admit, enr) else "news_control")
    if not rule.news_why_not(admit, enr, use_avoid=False):
        out.append("news_pick_no_avoid")
    if not rule.news_why_not(admit, enr, source="burst"):
        out.append("burst_pick")
    if enr.get("news_ranks_live") is not None:
        out.append("v2_all")
        out.append("news_pick_v2" if not rule.news_why_not(admit, enr, ranks="news_ranks_live") else "news_control_v2")
        if not rule.news_why_not(admit, enr, source="burst", ranks="news_ranks_live"):
            out.append("burst_pick_v2")
        if not rule.news_why_not(admit, enr, ranks="news_ranks_live") \
                or not rule.news_why_not(admit, enr, source="burst", ranks="news_ranks_live"):
            out.append("any_pick_v2")
    return out


def main_news(pattern: str, clip: float, fee: float) -> None:
    coins = load_news(pattern)
    print(f"{len(coins)} news candidates admitted+enriched")
    why = defaultdict(int)
    for c in coins.values():
        why[rule.news_why_not(c["admit"], c["enrich"]) or "PICK"] += 1
    print("news rule outcome:", dict(why))
    for h in HOLDS_H:
        by: dict[str, list[dict]] = defaultdict(list)
        for c in coins.values():
            t = trade(c, h, clip, fee)
            if t is not None:
                for g in news_groups(c["admit"], c["enrich"]):
                    by[g].append(t)
        print(f"\nhold {h}h{'  <- PRIMARY' if h == PRIMARY_H else ''}")
        for g in NEWS_GROUPS:
            print(line(g, by.get(g, [])))
    by = defaultdict(list)
    for c in coins.values():
        t = ladder_trade(c, clip, fee)
        if t is not None:
            for g in news_groups(c["admit"], c["enrich"]):
                by[g].append(t)
    print("\nladder exit (exits.Ladder)  <- PRIMARY for v2")
    for g in NEWS_GROUPS:
        print(line(g, by.get(g, [])))


NEWS_GROUPS = ("news_pick", "news_pick_no_avoid", "burst_pick", "news_control", "news_all",
               "news_pick_v2", "burst_pick_v2", "any_pick_v2", "news_control_v2", "v2_all")


MIGRATE_N, MIGRATE_LIQ, LOST_S = 3, 5_000, 1800


class Pin:
    """Follow one pool of a coin. The tracker re-picks a coin's deepest pair every minute and flapped between
    pools (a DBC curve, its PumpSwap or Meteora pool, thin copies): 119 of 368 news coins on 10-23/24 switched,
    157 switches jumped the price > 3x in one minute (XFILMS 1.08x -> 13.9x -> 0.64x on an $8k pool). Keep the
    first pool; move to another only once it has led for MIGRATE_N snapshots in a row with >= $5k liquidity
    (a real migration), and drop the off-pool snapshots before that. A pool unseen for 30 min (drained, so no
    longer the deepest) hands over to whatever is reported, so a rug is not dropped from the sample."""

    def __init__(self) -> None:
        self.cur, self.run, self.started, self.last = None, [], False, 0.0

    def feed(self, s: dict) -> list[dict]:
        """The snapshots to accept now (none, `s`, or a migration's run ending in `s`)."""
        if not self.started or s.get("pair") == self.cur or s["ts"] - self.last >= LOST_S:
            self.cur, self.run, self.started, self.last = s.get("pair"), [], True, s["ts"]
            return [s]
        self.run = self.run + [s] if self.run and self.run[-1].get("pair") == s.get("pair") else [s]
        if len(self.run) >= MIGRATE_N and all((x.get("liq") or 0) >= MIGRATE_LIQ for x in self.run):
            out, self.cur, self.run, self.last = self.run, s.get("pair"), [], s["ts"]
            return out
        return []


def pinned(snaps: list[dict]) -> list[dict]:
    pin = Pin()
    return [x for s in snaps for x in pin.feed(s)]


def ladder_trade(c: dict, clip: float, fee: float) -> dict | None:
    """The price-driven exit from the first snapshot within 3 min of the decision, on one pool (`pinned`);
    None until it has closed."""
    t0 = c["enrich"]["ts"]
    snaps = pinned([s for s in c["snaps"] if s["ts"] >= t0])
    if not snaps or snaps[0]["ts"] > t0 + ENTRY_WINDOW_S:
        return None
    lad = exits.run(snaps, c["gone"], clip, fee)
    if lad is None or not lad.done:
        return None
    return {"net": lad.net(), "gross": None, "max": lad.max_mult - 1, "gone": lad.reason == "gone"}


UNKNOWN_LIQ_IMPACT = 0.05   # per leg, when no source reports the pool's liquidity (DBC coins' PumpSwap pools)


def leg_cost(liq: float, clip: float, fee: float) -> float:
    return fee + (clip / (liq / 2) if liq >= 100 else UNKNOWN_LIQ_IMPACT)


def trade(c: dict, hold_h: float, clip: float, fee: float) -> dict | None:
    t0 = c["enrich"]["ts"]
    entry = next((s for s in c["snaps"] if t0 <= s["ts"] <= t0 + ENTRY_WINDOW_S), None)
    if entry is None:
        return None
    t_exit = entry["ts"] + hold_h * 3600
    if any(entry["ts"] < g <= t_exit for g in c["gone"]):
        return {"net": -1.0, "gross": -1.0, "max": None, "gone": True}
    exit_ = next((s for s in c["snaps"] if t_exit <= s["ts"] <= t_exit + EXIT_SLACK_S), None)
    if exit_ is None:
        return None
    gross = exit_["px"] / entry["px"] - 1
    keep = (1 - min(leg_cost(entry["liq"], clip, fee), 1)) * (1 - min(leg_cost(exit_["liq"], clip, fee), 1))
    peak = max(s["px"] for s in c["snaps"] if entry["ts"] <= s["ts"] <= exit_["ts"])
    return {"net": (1 + gross) * keep - 1, "gross": gross, "max": peak / entry["px"] - 1, "gone": False}


def groups(admit: dict, enr: dict) -> list[str]:
    if rule.why_not(admit, enr) == "mc":
        return []
    out = ["control"] if rule.why_not(admit, enr) else ["primary"]
    v = rule.original_ranks(admit, enr)
    original = v.get("mc_rank") == 1 and v.get("t_rank") == 1
    out.append("no_story" if original else "vamps")
    if rule.jev_rule(admit, enr):
        out.append("jev")
    av = rule.avoid(admit, enr)
    if av is not None:
        out.append("avoid" if av else "keep")
        if "primary" in out and not av:
            out.append("primary_keep")
    if "primary" in out:
        top10 = (enr.get("holders") or {}).get("top10")
        if top10 is not None:
            out.append("primary_float_hi" if top10 > 0.5 else "primary_float_lo")
    return out


def session(hour: int) -> str:
    return "asia" if hour >= 23 or hour < 7 else "europe" if hour < 13 else "us"


def ci(xs: list[float], n_boot: int = 2000) -> tuple[float, float]:
    if len(xs) < 5:
        return float("nan"), float("nan")
    rng = random.Random(7)
    means = sorted(statistics.fmean(rng.choices(xs, k=len(xs))) for _ in range(n_boot))
    return means[int(0.025 * n_boot)], means[int(0.975 * n_boot)]


def line(name: str, ts: list[dict]) -> str:
    if not ts:
        return f"  {name:<18} n=0"
    net = [t["net"] for t in ts]
    lo, hi = ci(net)
    mx = [t["max"] for t in ts if t["max"] is not None]
    return (f"  {name:<18} n={len(ts):<4} mean {statistics.fmean(net):+7.1%} CI [{lo:+.1%},{hi:+.1%}]  "
            f"median {statistics.median(net):+7.1%}  win {sum(x > 0 for x in net) / len(net):5.1%}  "
            f"2x-touch {sum(m >= 1 for m in mx) / max(len(mx), 1):5.1%}  gone {sum(t['gone'] for t in ts)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pattern")
    ap.add_argument("--clip-usd", type=float, default=100.0)
    ap.add_argument("--fee", type=float, default=0.01, help="per leg: venue fee + priority/tip + slippage slack")
    ap.add_argument("--news", action="store_true", help="score the news-first candidates instead")
    a = ap.parse_args()
    if a.news:
        main_news(a.pattern, a.clip_usd, a.fee)
        return
    coins = load(a.pattern)
    admits = [c["admit"]["ts"] for c in coins.values()]
    if not admits:
        print("no admitted+enriched coins yet")
        return
    print(f"{len(coins)} coins admitted+enriched, {min(admits):.0f}..{max(admits):.0f} "
          f"({(max(admits) - min(admits)) / 86400:.1f} days); clip ${a.clip_usd:.0f}, fee {a.fee:.1%}/leg")
    why = defaultdict(int)
    for c in coins.values():
        why[rule.why_not(c["admit"], c["enrich"]) or "PICK"] += 1
    print("rule outcome:", dict(why))
    for h in HOLDS_H:
        by: dict[str, list[dict]] = defaultdict(list)
        for c in coins.values():
            t = trade(c, h, a.clip_usd, a.fee)
            if t is None:
                continue
            for g in groups(c["admit"], c["enrich"]):
                by[g].append(t)
                if g == "primary":
                    by[f"primary_{session(c['admit'].get('hour_utc', 0))}"].append(t)
        print(f"\nhold {h}h{'  <- PRIMARY' if h == PRIMARY_H else ''}")
        for g in ("primary", "no_story", "jev", "vamps", "control", "avoid", "keep", "primary_keep",
                  "primary_float_hi", "primary_float_lo",
                  "primary_asia", "primary_europe", "primary_us"):
            print(line(g, by.get(g, [])))
        p, ctl = [t["net"] for t in by.get("primary", [])], [t["net"] for t in by.get("control", [])]
        if len(p) >= 5 and len(ctl) >= 5:
            rng = random.Random(11)
            d = sorted(statistics.fmean(rng.choices(p, k=len(p))) - statistics.fmean(rng.choices(ctl, k=len(ctl)))
                       for _ in range(2000))
            print(f"  primary - control  {statistics.fmean(p) - statistics.fmean(ctl):+.1%} CI [{d[50]:+.1%},{d[1949]:+.1%}]")
        k, av = [t["net"] for t in by.get("keep", [])], [t["net"] for t in by.get("avoid", [])]
        if len(k) >= 5 and len(av) >= 5:
            rng = random.Random(13)
            d = sorted(statistics.fmean(rng.choices(k, k=len(k))) - statistics.fmean(rng.choices(av, k=len(av)))
                       for _ in range(2000))
            print(f"  keep - avoid       {statistics.fmean(k) - statistics.fmean(av):+.1%} CI [{d[50]:+.1%},{d[1949]:+.1%}]")


if __name__ == "__main__":
    main()
