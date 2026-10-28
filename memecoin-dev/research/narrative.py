"""EXPLORATORY: is being FIRST on a new narrative worth more than copying it?
A term is NEW if no coin used it in the previous 6 h; a narrative CAUGHT ON if 3+ other creators used the term
within 1 h of the first coin. Leader = the first coin on such a term; followers = the next coins on it.
Outcome: peak market cap in the first hour (from the curve after every trade), creator fee (non-agent volume).
Mayhem coins (the agent traded them) are dropped. The first 6 h of the tape are warm-up only."""
import collections, gzip, sys
import numpy as np
import orjson
sys.path.insert(0, "..")
from devbot.trends import terms

AGENT = "BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s"
SOL_USD = float(sys.argv[2])
NEW_S, CATCH_S, HOUR = 6 * 3600, 3600, 3600

creates, peak, vol, mayhem = {}, collections.defaultdict(float), collections.defaultdict(float), set()
with gzip.open(sys.argv[1], "rb") as f:
    for line in f:
        try: d = orjson.loads(line)
        except orjson.JSONDecodeError: continue
        t = d.get("t")
        if t == "create":
            creates[d["mint"]] = d
        elif t == "trade" and d["mint"] in creates:
            c = creates[d["mint"]]
            if d["ts"] - c["ts"] > HOUR: continue
            if d["user"] == AGENT: mayhem.add(d["mint"]); continue
            if d.get("v_sol") and d.get("v_tok"):
                peak[d["mint"]] = max(peak[d["mint"]], d["v_sol"] / d["v_tok"] * 1e9)   # mcap in SOL, 1B supply
            if d["user"] != c["creator"]: vol[d["mint"]] += float(d["sol"])
order = sorted((c for m, c in creates.items() if m not in mayhem), key=lambda c: c["ts"])
t0 = order[0]["ts"]
by_term = collections.defaultdict(list)
for c in order:
    for term in terms(c.get("name") or "", c.get("symbol") or ""):
        by_term[term].append(c)
leaders, followers, firsts = {}, {}, {}
for term, cs in by_term.items():
    i = 0
    while i < len(cs):
        c = cs[i]
        fresh = c["ts"] - t0 >= NEW_S and (i == 0 or c["ts"] - cs[i - 1]["ts"] > NEW_S)
        if fresh:
            firsts.setdefault(c["mint"], c)
            wave = [x for x in cs[i + 1:] if x["ts"] - c["ts"] <= CATCH_S]
            if len({x["creator"] for x in wave} - {c["creator"]}) >= 3:
                leaders[c["mint"]] = c
                for x in wave:
                    if x["creator"] != c["creator"]: followers.setdefault(x["mint"], x)
        i += 1
for m in leaders: followers.pop(m, None)
base = [c for c in order if c["ts"] - t0 >= NEW_S]
def row(lab, cs):
    pk = np.array([peak.get(c["mint"], 0.0) * SOL_USD for c in cs]); fee = np.array([0.003 * vol.get(c["mint"], 0.0) for c in cs])
    print(f"{lab:34} n {len(cs):6,} | median peak ${np.median(pk):8,.0f} | P(peak>=$40k) {np.mean(pk >= 40000):6.2%} | "
          f"P(>=$100k) {np.mean(pk >= 100000):6.2%} | mean fee {fee.mean():.4f} SOL | median fee {np.median(fee):.5f}")
print(f"#### {sys.argv[1].split('/')[-1]}  (SOL ${SOL_USD:.0f}; peak mcap in the first hour)")
row("all launches (after warm-up)", base)
row("LEADER: first on a new term", list(leaders.values()))
row("FOLLOWER: later coin on that term", list(followers.values()))
# the fair test: first on a new term, BEFORE anyone knows if it catches on. A coin's name is often a new word,
# so also require the term to be used by another creator at some point earlier in the tape (a known word, newly hot).
seen_before = collections.defaultdict(set)
known = {}
for c in order:
    for term in terms(c.get("name") or "", c.get("symbol") or ""):
        if seen_before[term] - {c["creator"]}:
            known.setdefault(c["mint"], c)
        seen_before[term].add(c["creator"])
row("first on new term, UNCONDITIONAL", list(firsts.values()))
row("  ...and the term is a known word", [c for m, c in firsts.items() if m in known])
print(f"P(a first-on-new-term coin's term catches on) = {len(leaders) / max(len(firsts), 1):.2%}")
hits = [c for c in base if peak.get(c["mint"], 0) * SOL_USD >= 40000]
print(f"of {len(hits):,} coins that reached $40k: {sum(c['mint'] in leaders for c in hits):,} leaders, "
      f"{sum(c['mint'] in followers for c in hits):,} followers of a caught-on term")
