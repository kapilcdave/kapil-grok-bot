"""Can decision-time features pick the winners among post-linked launches? Label = REALISED return of the exit.

Decision at create+DELAY using only trades/posts visible by then. Entry = post-state price of the first trade at
>= decision + LAT (our tx lands later). Exit = first touch of 2x (sell at 2x) / 0.6x (sell at 0.6x), else mark at
the last price within HORIZON. COST = round-trip fee+slippage as a fraction. Time split on create time, both
directions. Selection quantiles are taken on the HOLDOUT's own score ranking (no threshold tuned on test).

Usage: python3 research/postmodel.py [delay] [--all]   (--all: do not restrict to post-linked launches)
"""
import bisect, json, math, os, pickle, random, sys
from collections import defaultdict

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor

args = [a for a in sys.argv[1:] if not a.startswith('--')]
DELAY = float(args[0]) if args else 15.0
ALL = '--all' in sys.argv
AGENT = 'BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s'
LAT, HORIZON, COST = 0.5, 3600, 0.03
WIN, STOP = 2.0, 0.6
T0, T1 = 1791969960, 1792002300
TAPE = '/Users/kapil/tapes/narr-tape-20261014T092453Z.jsonl'
POSTS = '/Users/kapil/tapes/narr-posts-20261014T092453Z.jsonl'
CACHE = '/private/tmp/claude-501/-Users-kapil-proj/695ebb84-3cbe-4707-984e-2e3a5babac62/scratchpad/postmodel_cache.pkl'


def xts(i):
    return ((int(i) >> 22) + 1288834974657) / 1000


if os.path.exists(CACHE):
    creates, trades, post_of = pickle.load(open(CACHE, 'rb'))
else:
    creates, trades, post_of = {}, defaultdict(list), {}
    for l in open(TAPE):
        try: r = json.loads(l)
        except ValueError: continue
        t = r.get('t')
        if t == 'create' and T0 <= r['ts'] <= T1: creates[r['mint']] = r
        elif t == 'trade': trades[r['mint']].append(r)
    for l in open(POSTS):
        try: r = json.loads(l)
        except ValueError: continue
        if r.get('t') == 'ref' and r.get('id'): post_of.setdefault(r['mint'], r['id'])
    pickle.dump((creates, dict(trades), post_of), open(CACHE, 'wb'))

by_post = defaultdict(list)
for m, p in post_of.items():
    if m in creates: by_post[p].append((creates[m]['ts'], m))
for p in by_post: by_post[p].sort()
creator_hist = defaultdict(list)
for m, c in sorted(creates.items(), key=lambda kv: kv[1]['ts']): creator_hist[c['creator']].append(c['ts'])

rows = []
for m, c in creates.items():
    if not ALL and m not in post_of: continue
    t_c = c['ts']; td = t_c + DELAY
    tr = sorted((x for x in trades.get(m, []) if x['ts'] - t_c <= HORIZON), key=lambda x: x['ts'])
    if sum(x['sol'] for x in tr if x['user'] == c['creator'] and x['side'] == 'buy') >= 50: continue
    if any(x['user'] == AGENT for x in tr): continue     # Mayhem
    ts = [x['ts'] for x in tr]
    i = bisect.bisect_right(ts, td + LAT)
    if i >= len(tr): continue          # no fill observable
    pre = [x for x in tr if x['ts'] <= td]
    entry = tr[i]['v_sol'] / tr[i]['v_tok']
    R = None; res = 'open'
    for x in tr[i:]:
        p = x['v_sol'] / x['v_tok']
        if p >= WIN * entry: R = WIN - 1; res = 'win'; break
        if p <= STOP * entry: R = STOP - 1; res = 'stop'; break
    if R is None: R = tr[-1]['v_sol'] / tr[-1]['v_tok'] / entry - 1
    R -= COST
    pid = post_of.get(m)
    earlier = [mm for tt, mm in by_post.get(pid, []) if tt < t_c] if pid else []
    buys = [x for x in pre if x['side'] == 'buy' and x['user'] != c['creator']]
    p0 = (c['v_sol'] / c['v_tok']) if c.get('v_tok') else None
    pd = (pre[-1]['v_sol'] / pre[-1]['v_tok']) if pre else p0
    f = dict(
        age=(t_c - xts(pid)) if pid else -1, has_post=int(bool(pid)), n_earlier_on_post=len(earlier), n_on_post=len(by_post.get(pid, [])) if pid else 0,
        creator_prior=bisect.bisect_left(creator_hist[c['creator']], t_c),
        dev_buy=sum(x['sol'] for x in pre if x['user'] == c['creator'] and x['side'] == 'buy'),
        n_tr=len(pre), n_buyers=len({x['user'] for x in buys}), buy_sol=sum(x['sol'] for x in buys),
        n_sells=sum(1 for x in pre if x['side'] == 'sell'), max_buy=max([x['sol'] for x in buys] or [0]),
        px_ratio=(pd / p0) if p0 and pd else 1.0, first_buy_lag=(buys[0]['ts'] - t_c) if buys else DELAY * 2,
        v_sol_now=pre[-1]['v_sol'] if pre else 30.0, hour=(t_c % 86400) / 3600,
    )
    rows.append(dict(m=m, t_c=t_c, R=R, res=res, f=f))
rows.sort(key=lambda r: r['t_c'])
names = list(rows[0]['f'])
X = np.array([[r['f'][k] for k in names] for r in rows], float); y = np.array([r['R'] for r in rows])
win = np.array([r['res'] == 'win' for r in rows])
n = len(rows)
print(f"delay {DELAY}s  scope {'ALL launches' if ALL else 'post-linked'}  n={n}  base: mean R {y.mean():+.1%}  win {win.mean():.1%}  stop {np.mean([r['res']=='stop' for r in rows]):.1%}")


def boot(v, B=2000, seed=1):
    rng = random.Random(seed); v = list(v); m = len(v)
    s = sorted(sum(v[rng.randrange(m)] for _ in range(m)) / m for _ in range(B))
    return s[int(.025 * B)], s[int(.975 * B)]


cut = int(n * .6)
for name, tr_idx, te_idx in (('fit first 60% -> test last 40%', range(0, cut), range(cut, n)),
                             ('fit last 40% -> test first 60%', range(cut, n), range(0, cut))):
    tr_idx, te_idx = list(tr_idx), list(te_idx)
    mdl = HistGradientBoostingRegressor(max_depth=3, learning_rate=0.05, max_iter=150, min_samples_leaf=40, random_state=0)
    mdl.fit(X[tr_idx], y[tr_idx])
    s = mdl.predict(X[te_idx]); order = np.argsort(-s)
    print(f"\n{name}: n_test={len(te_idx)} test base mean R {y[te_idx].mean():+.1%} win {win[te_idx].mean():.1%}")
    for q in (0.02, 0.05, 0.10, 0.20, 0.50):
        k = max(10, int(len(te_idx) * q)); sel = [te_idx[j] for j in order[:k]]
        lo, hi = boot(y[sel])
        print(f"  top {q:>4.0%} (k={k:>4})  mean R {y[sel].mean():+7.1%} [{lo:+.1%},{hi:+.1%}]  win {win[sel].mean():5.1%}")
