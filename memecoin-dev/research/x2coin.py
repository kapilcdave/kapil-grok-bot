"""Join j7 tweets to name-matched new pump.fun coins and score the ladder outcome. See PREREG-x2coin-20261024.md.

Usage: python3 research/x2coin.py --j7 research/data/j7/j7tape-*.jsonl --chain ~/tapes/x2c-chain-*.jsonl
"""
import argparse, bisect, glob, json, math, random, re
from collections import defaultdict

LAT, HORIZON, COST, WIN, STOP = 0.5, 3600, 0.03, 2.0, 0.6
DELAYS = (5, 15, 30, 60, 120)
WINDOW = 1800


def norm(s):
    return re.sub(r'[^a-z0-9]', '', (s or '').lower())


def wilson(k, n, z=1.96):
    if not n: return (0.0, 0.0)
    p = k / n; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d; h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def boot(v, B=2000):
    rng = random.Random(1); v = list(v); m = len(v)
    if m < 2: return (float('nan'),) * 2
    s = sorted(sum(v[rng.randrange(m)] for _ in range(m)) / m for _ in range(B))
    return s[int(.025 * B)], s[int(.975 * B)]


def q(a, ps=(.1, .25, .5, .75, .9)):
    a = sorted(a); return [round(a[int(p * (len(a) - 1))], 1) for p in ps] if a else []


def load_j7(paths):
    tw, sug = {}, {}
    for p in paths:
        for l in open(p):
            try: r = json.loads(l)
            except ValueError: continue
            e, d = r['event'], r['data']
            if e == 'tweet' and d.get('type') in ('TWEET', 'QUOTE'):
                tw.setdefault(d['id'], dict(id=d['id'], t=d['createdAt'] / 1000, recv=r['recv_ns'] / 1e9, handle=d['author']['handle'],
                                            fol=d['author'].get('followersCount') or 0, text=d.get('text', '')))
            elif e == 'ai_suggestion' and d.get('tweet_id'):
                sug.setdefault(d['tweet_id'], d)
    out = []
    for i, t in tw.items():
        s = sug.get(i)
        if not s: continue
        t['keys'] = {k for k in (norm(s.get('prediction')), norm(s.get('ticker')), norm(s.get('ai_ticker'))) if len(k) >= 3}
        if t['keys']: out.append(t)
    return sorted(out, key=lambda t: t['t'])


def load_chain(paths):
    creates, trades = {}, defaultdict(list)
    for p in paths:
        for l in open(p):
            try: r = json.loads(l)
            except ValueError: continue
            if r.get('t') == 'create': creates[r['mint']] = r
            elif r.get('t') == 'trade': trades[r['mint']].append(r)
    return creates, trades


def path(m, c, trades):
    tr = sorted((x for x in trades.get(m, []) if x['ts'] - c['ts'] <= HORIZON), key=lambda x: x['ts'])
    if sum(x['sol'] for x in tr if x['user'] == c['creator'] and x['side'] == 'buy') >= 50: return None
    return [x['ts'] for x in tr], [x['v_sol'] / x['v_tok'] for x in tr if x['v_tok']]


def outcome(ts, px, t_in):
    i = bisect.bisect_right(ts, t_in + LAT)
    if i >= len(px): return None
    e = px[i]
    for p in px[i:]:
        if p >= WIN * e: return 'win', WIN - 1 - COST
        if p <= STOP * e: return 'stop', STOP - 1 - COST
    return 'open', px[-1] / e - 1 - COST


def summarise(name, items, delay):
    res = []
    for ts, px, t0 in items:
        o = outcome(ts, px, t0 + delay)
        if o: res.append(o)
    n = len(res); w = sum(1 for r in res if r[0] == 'win')
    if not n: return f"  {name:<34} n=0"
    lo, hi = wilson(w, n); blo, bhi = boot([r[1] for r in res])
    return (f"  {name:<34} n={n:>5} (dropped {len(items)-n:>4})  win {w/n:6.1%} [{lo:5.1%},{hi:5.1%}]  "
            f"stop {sum(1 for r in res if r[0]=='stop')/n:5.1%}  mean R {sum(r[1] for r in res)/n:+7.1%} [{blo:+.1%},{bhi:+.1%}]")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--j7', nargs='+', required=True); ap.add_argument('--chain', nargs='+', required=True)
    a = ap.parse_args()
    tw = load_j7([f for g in a.j7 for f in glob.glob(g)]); creates, trades = load_chain([f for g in a.chain for f in glob.glob(g)])
    t_lo, t_hi = min(c['ts'] for c in creates.values()), max(c['ts'] for c in creates.values())
    tw = [t for t in tw if t['t'] >= t_lo - 5 and t['t'] <= t_hi - 120]    # tweets the chain tape could have followed
    print(f"{len(tw)} tweets with an AI suggestion inside the chain-tape window; {len(creates)} creates, {len(trades)} mints with trades")
    by_key = defaultdict(list)
    for m, c in creates.items():
        for k in {norm(c.get('name')), norm(c.get('symbol'))}:
            if len(k) >= 3: by_key[k].append(c)
    linked = {}                      # mint -> (tweet, rank on that tweet)
    for t in tw:
        cs = sorted({c['mint']: c for k in t['keys'] for c in by_key.get(k, []) if t['t'] - 5 <= c['ts'] <= t['t'] + WINDOW}.values(), key=lambda c: c['ts'])
        for r, c in enumerate(cs):
            linked.setdefault(c['mint'], (t, r))
    tweets_hit = {v[0]['id'] for v in linked.values()}
    print(f"{len(linked)} linked coins from {len(tweets_hit)} tweets ({len(tweets_hit)/max(len(tw),1):.0%} of tweets spawned a name-matched coin within {WINDOW//60} min)")
    lag = [creates[m]['ts'] - t['t'] for m, (t, r) in linked.items()]
    print("seconds tweet(X createdAt) -> coin create  p10/25/50/75/90:", q(lag))

    L, C, LF, LL, BIG, SMALL = [], [], [], [], [], []
    for m, c in creates.items():
        pp = path(m, c, trades)
        if not pp or not pp[1]: continue
        if m in linked:
            t, r = linked[m]; t0 = max(c['ts'], t['t'])
            row = (pp[0], pp[1], t0); L.append(row); (LF if r == 0 else LL).append(row); (BIG if t['fol'] >= 1e6 else SMALL).append(row)
        else:
            C.append((pp[0], pp[1], c['ts']))
    for d in DELAYS:
        print(f"\nDELAY {d}s")
        for name, items in (('LINKED', L), ('CONTROL (unlinked creates)', C), ('linked, first on tweet', LF), ('linked, later on tweet', LL),
                            ('linked, author >= 1M followers', BIG), ('linked, author < 1M', SMALL)):
            print(summarise(name, items, d))

    jl_t, jl_c = [], []
    for m, (t, r) in linked.items():
        c = creates[m]; pp = path(m, c, trades)
        if not pp or len(pp[1]) < 2: continue
        ts, px = pp; i = bisect.bisect_left(ts, c['ts'] + 5)
        if i >= len(px): continue
        for tt, p in zip(ts[i:], px[i:]):
            if p >= WIN * px[i]: jl_t.append(tt - t['t']); jl_c.append(tt - c['ts']); break
    print(f"\nJUMP (price >= 2x the create+5s price) in {len(jl_t)} of {len(linked)} linked coins")
    print("  seconds from TWEET to jump  p10/25/50/75/90:", q(jl_t)); print("  seconds from CREATE to jump p10/25/50/75/90:", q(jl_c))
    print("\nlinked coins that jumped (tweet text -> coin):")
    for m, (t, r) in linked.items():
        c = creates[m]; pp = path(m, c, trades)
        if pp and pp[1] and max(pp[1]) >= 2 * pp[1][0]:
            print(f"  @{t['handle']} ({t['fol']:,}) +{c['ts']-t['t']:.0f}s rank{r} {c.get('name')!r}/{c.get('symbol')!r} peak {max(pp[1])/pp[1][0]:.1f}x | {t['text'][:70]!r}")


if __name__ == '__main__':
    main()
