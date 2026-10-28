import collections, sys
import numpy as np
import levers as L
days = {}
for p in sys.argv[1:]:
    cr, me, tr = L.load(p)
    b = collections.defaultdict(set)
    for mint, rows in tr.items():
        if mint in cr:
            for ts, user, side, sol, sig in rows:
                if side == "buy": b[user].add(mint)
    days[p] = (cr, tr, b)
(p1, (cr1, tr1, b1)), (p2, (cr2, tr2, b2)) = days.items()
other = set(b2)
print(f"wallets buying on day1: {len(b1):,}; also buying on day2: {len(set(b1) & other):,} ({len(set(b1) & other)/len(b1):.1%})")
low = [u for u, s in b1.items() if len(s) <= 10]
print(f"low-activity (<=10 mints) wallets day1: {len(low):,}; of those seen again day2: {np.mean([u in other for u in low]):.1%}")
# top coins by low-activity buyers on day1: what share return on day2, and when they bought
for mint, c in cr1.items():
    pass
res = []
for mint, c in cr1.items():
    t0 = c["ts"]; first = {}
    for ts, user, side, sol, sig in tr1.get(mint, ()):
        if side == "buy" and user != c["creator"] and ts - t0 <= 3600: first.setdefault(user, ts)
    hl = {u: ts - t0 for u, ts in first.items() if ts - t0 >= 2 and len(b1[u]) <= 10}
    if len(hl) >= 50: res.append((len(hl), c["name"], np.mean([u in other for u in hl]), np.median(list(hl.values())),
                                  np.mean([len(b1[u]) == 1 for u in hl])))
res.sort(reverse=True)
print("coin (low-activity buyers): share seen again next day | median delay | share that bought ONLY this coin all day")
for n, name, ret, med, only in res[:12] + res[len(res)//2: len(res)//2 + 6]:
    print(f"  {n:5} {name[:24]!r:27} returns {ret:6.1%}  median {med:5.0f}s  single-coin {only:6.1%}")
