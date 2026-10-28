"""Score j7px.py rows: does an existing coin that j7 matches to a tweet move after the tweet?

Per (tweet, mint) with a positive mc at offset 0: news-window return = mc[k]/mc[0] for k in OFFSETS; placebo = the
same coin's mc[4200]/mc[3600] (an hour later, no news). Coins with no price at 0 (dead, no pool) are counted, not
scored. Reported with and without a liquidity floor. Read once at the end; no tuning on these rows.

    python3 research/j7px_score.py ~/tapes/j7px-20261024.jsonl
"""
import json, math, random, sys
from collections import defaultdict

rows = defaultdict(dict)           # (tweet, mint) -> {k: row}
for l in open(sys.argv[1]):
    r = json.loads(l); rows[(r['tweet'], r['mint'])][r['k']] = r
KS = (30, 60, 120, 300, 600, 1800, 3600)


def boot_mean(v, B=2000):
    rng = random.Random(1); m = len(v)
    if m < 3: return (float('nan'),) * 2
    s = sorted(sum(v[rng.randrange(m)] for _ in range(m)) / m for _ in range(B))
    return s[int(.025 * B)], s[int(.975 * B)]


def report(name, floor):
    pairs = [(k, v) for k, v in rows.items() if v.get(0, {}).get('mc') and (v[0].get('liq') or 0) >= floor]
    print(f"\n== {name}: {len(pairs)} priced (tweet, mint) pairs of {len(rows)} tracked ({len({k[1] for k, _ in pairs})} unique coins)")
    for k in KS:
        lr = [math.log(v[k]['mc'] / v[0]['mc']) for _, v in pairs if v.get(k, {}).get('mc') and v[0]['mc'] > 0]
        if len(lr) < 3: print(f"  +{k:>4}s  n={len(lr)}"); continue
        lo, hi = boot_mean(lr); up2 = sum(1 for x in lr if x >= math.log(2)) / len(lr)
        print(f"  +{k:>4}s  n={len(lr):>4}  mean log-ret {sum(lr)/len(lr):+.3f} [{lo:+.3f},{hi:+.3f}]  median {sorted(lr)[len(lr)//2]:+.3f}  >=2x {up2:5.1%}")
    pl = [math.log(v[4200]['mc'] / v[3600]['mc']) for _, v in pairs if v.get(4200, {}).get('mc') and v.get(3600, {}).get('mc')]
    if len(pl) >= 3:
        lo, hi = boot_mean(pl); print(f"  PLACEBO +3600->+4200s (600 s, same coins)  n={len(pl)}  mean log-ret {sum(pl)/len(pl):+.3f} [{lo:+.3f},{hi:+.3f}]  >=2x {sum(1 for x in pl if x >= math.log(2))/len(pl):5.1%}")
    nw = [math.log(v[600]['mc'] / v[0]['mc']) for _, v in pairs if v.get(600, {}).get('mc')]
    if len(nw) >= 3: print(f"  (compare the news window +0->+600s n={len(nw)}: mean {sum(nw)/len(nw):+.3f}, >=2x {sum(1 for x in nw if x >= math.log(2))/len(nw):5.1%})")


report('all priced', 0)
report('liquidity >= $10k', 10000)
