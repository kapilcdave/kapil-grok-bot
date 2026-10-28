"""Descriptive pass over a snipefeed tape: no outcome-by-feature reads (those wait for the prereg)."""
import collections, gzip, sys
from urllib.parse import urlparse
import orjson

path = sys.argv[1]
creates, metas, buys_by_wallet, first_seen = {}, {}, collections.defaultdict(set), {}
n_trade = 0
with gzip.open(path, "rb") as f:
    for line in f:
        try:
            d = orjson.loads(line)
        except orjson.JSONDecodeError:
            continue
        t = d.get("t")
        if t == "create":
            creates[d["mint"]] = d
        elif t == "meta":
            metas[d["mint"]] = d
        elif t == "trade":
            n_trade += 1
            if d["side"] == "buy" and d["mint"] in creates:
                buys_by_wallet[d["user"]].add(d["mint"])
print(f"creates {len(creates):,}  metas {len(metas):,} (ok {sum(m.get('ok', False) for m in metas.values()):,})  trades {n_trade:,}")
mayhem = sum(abs(c["v_sol"] * c["v_tok"] / (30e9 * 1.073e15 / 1e9) - 1) > 0.01 for c in creates.values())
print(f"non-standard (Mayhem) creates by vs*vt: {mayhem:,}")
k = collections.Counter(min(len(v), 1000) for v in buys_by_wallet.values())
tot = len(buys_by_wallet)
for lo, hi in ((1, 1), (2, 3), (4, 10), (11, 30), (31, 100), (101, 300), (301, 1000)):
    n = sum(c for m, c in k.items() if lo <= m <= hi)
    print(f"wallets buying {lo}-{hi} mints/day: {n:,} ({n / tot:.1%})")
def tw_kind(u):
    if not u: return "none"
    u = u.lower()
    if "/communities/" in u: return "x_community"
    if "/status/" in u: return "x_post"
    if "x.com/" in u or "twitter.com/" in u: return "x_profile"
    return "other"
ok = [m for m in metas.values() if m.get("ok")]
print("twitter:", collections.Counter(tw_kind(m.get("twitter")) for m in ok).most_common())
print("website:", sum(bool(m.get("website")) for m in ok), "telegram:", sum(bool(m.get("telegram")) for m in ok), "desc:", sum(bool(m.get("description")) for m in ok), "of", len(ok))
print("uri hosts:", collections.Counter(urlparse(c.get("uri") or "").netloc for c in creates.values()).most_common(8))
cr = collections.Counter(c["creator"] for c in creates.values())
print("creators:", len(cr), "| 1 launch:", sum(v == 1 for v in cr.values()), "| 2-9:", sum(2 <= v <= 9 for v in cr.values()), "| 10+:", sum(v >= 10 for v in cr.values()))
