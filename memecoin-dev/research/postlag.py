"""Post -> create -> jump lag on the 10-14 curve tape, every launch in the denominator.

Question (operator 10-24): how long after an X post does a coin spawned off it jump, and does buying at a
chosen delay win? Winner = price touches 2x the entry price before it touches 0.6x, within HORIZON seconds.
Entry = first observed trade price at or after create+delay (curve price from v_sol/v_tok; supply fixed 1e9).
Self-graduated brand coins (creator buys >= 50 SOL in 1 h) are dropped, like narrscore.py.
Coins whose first-hour trades were not watched (no trade at or after the entry time) are excluded from n, never
counted as losses.

Posts come from the posts file (`ref` rows) when one is given, else from the tape's own `meta` rows (twitter status URL).
Mayhem coins (the pump.fun agent trades them in the first hour) are dropped from every group.

Usage: python3 research/postlag.py [tape] [posts|-]
"""
import bisect, json, math, sys
from collections import defaultdict

args = [a for a in sys.argv[1:] if not a.startswith('--')]
TAPE = args[0] if args else '/Users/kapil/tapes/narr-tape-20261014T092453Z.jsonl'
POSTS = args[1] if len(args) > 1 else '/Users/kapil/tapes/narr-posts-20261014T092453Z.jsonl'
if POSTS == '-': POSTS = None
AGENT = 'BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s'
HORIZON = 3600
DELAYS = (0, 5, 15, 30, 60, 120, 300)
WIN, STOP = 2.0, 0.6
T0, T1 = (1791969960, 1792002300) if POSTS else (0, 1e12)   # 10-14 clean window 09:26Z..18:25Z; else the whole tape
STATUS = __import__('re').compile(r'/status/(\d+)')


def xts(i):
    return ((int(i) >> 22) + 1288834974657) / 1000


def wilson(k, n, z=1.96):
    if not n: return (0, 0)
    p = k / n; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d; h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


creates, trades, post_of = {}, defaultdict(list), {}
with open(TAPE) as f:
    for l in f:
        try: r = json.loads(l)
        except ValueError: continue
        t = r.get('t')
        if t == 'create' and T0 <= r['ts'] <= T1: creates[r['mint']] = r
        elif t == 'trade': trades[r['mint']].append(r)
        elif t == 'meta' and not POSTS and r.get('ok') and r.get('twitter'):
            m_ = STATUS.search(r['twitter'])
            if m_: post_of.setdefault(r['mint'], m_.group(1))
if POSTS:
    with open(POSTS) as f:
        for l in f:
            try: r = json.loads(l)
            except ValueError: continue
            if r.get('t') == 'ref' and r.get('id'): post_of.setdefault(r['mint'], r['id'])
print(f"{len(creates)} creates in window, {sum(m in post_of for m in creates)} with a linked post, {len(trades)} mints with trades")

rows = []
for m, c in creates.items():
    t_c = c['ts']
    tr = sorted((x for x in trades.get(m, []) if x['ts'] - t_c <= HORIZON), key=lambda x: x['ts'])
    if sum(x['sol'] for x in tr if x['user'] == c['creator'] and x['side'] == 'buy') >= 50: continue
    if any(x['user'] == AGENT for x in tr): continue
    px = [(x['ts'], x['v_sol'] / x['v_tok']) for x in tr if x['v_tok']]
    rows.append(dict(m=m, t_c=t_c, px=px, ts=[p[0] for p in px], ntr=len(tr), post=post_of.get(m)))
print(f"{len(rows)} launches after drops; {sum(1 for r in rows if r['ntr'] >= 2)} with >=2 trades in the first hour")


def outcome(r, t_in):
    px = r['px']; i = bisect.bisect_left(r['ts'], t_in)
    if i >= len(px): return None
    e = px[i][1]
    for _, p in px[i:]:
        if p >= WIN * e: return 'win'
        if p <= STOP * e: return 'stop'
    return 'open'


def table(name, sel):
    print(f"\n== {name}  (n launches {len(sel)})")
    for d in DELAYS:
        res = [x for x in (outcome(r, r['t_c'] + d) for r in sel) if x]
        w = res.count('win'); s = res.count('stop'); n = len(res)
        lo, hi = wilson(w, n)
        print(f"  buy create+{d:>3}s  n={n:>5}  win {w/max(n,1):6.1%} [{lo:5.1%},{hi:5.1%}]  stop-first {s/max(n,1):5.1%}  unresolved {(n-w-s)/max(n,1):5.1%}")


withpost = [r for r in rows if r['post']]
for r in withpost: r['age'] = r['t_c'] - xts(r['post'])
table('ALL launches', rows)
table('launches linking a post', withpost)
table('no post', [r for r in rows if not r['post']])
for lo, hi in ((-1e9, 0), (0, 5), (5, 15), (15, 60), (60, 600), (600, 1e9)):
    table(f"post age at create in [{lo},{hi})s", [r for r in withpost if lo <= r['age'] < hi])

# post -> jump lag: first time the price is >= 2x the price at create+5s, for coins that did it
lags_post, lags_create = [], []
for r in withpost:
    px = r['px']
    if len(px) < 2: continue
    base = next((p for t, p in px if t >= r['t_c'] + 5), None)
    if not base: continue
    for t, p in px:
        if t >= r['t_c'] + 5 and p >= WIN * base:
            lags_post.append(t - xts(r['post'])); lags_create.append(t - r['t_c']); break


def q(a, ps=(.1, .25, .5, .75, .9)):
    a = sorted(a); return [round(a[int(p * (len(a) - 1))], 1) for p in ps] if a else []


print(f"\nJUMP (price >= 2x the create+5s price): {len(lags_post)} of {len(withpost)} post-linked launches")
print("  seconds from POST to jump   p10/25/50/75/90:", q(lags_post))
print("  seconds from CREATE to jump p10/25/50/75/90:", q(lags_create))
