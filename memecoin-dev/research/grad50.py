"""Buy at graduation (~$50k mc), sell at $5M: base rate on the 10-14 curve tape, every graduate in the denominator.

Operator 10-26: "enter at 50k, sell at 5m". Graduate = first trade with curve v_sol >= 110 (graduation ~ v_sol 115).
Post-graduation path = GeckoTerminal 1-min candles of the PumpSwap pool (pool from DexScreener token-pairs).

  python3 research/grad50.py tape  [tape]            -> research/data/grad50_grads.json
  python3 research/grad50.py fetch [--limit N]       -> research/data/grad50_candles.jsonl (resumable)
  python3 research/grad50.py score                   -> table

Flags per graduate: mayhem (AGENT trades in first hour), selfgrad (creator bought >= 50 SOL), organic buyers before grad.
Entry = open of the first full minute after grad + 5 s; stop = low <= 0.6 x entry, assumed to fire BEFORE any target inside
the same candle (conservative); target = mc >= TARGET_MC (5M) or K x entry.
"""
import json, sys, time, collections, math
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from devbot import http                                    # noqa: E402

DATA = Path(__file__).resolve().parent / 'data'
GRADS, CANDLES = DATA / 'grad50_grads.json', DATA / 'grad50_candles.jsonl'
AGENT = 'BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s'
GT = 'https://api.geckoterminal.com/api/v2/networks/solana'
DS = 'https://api.dexscreener.com'
SUPPLY, TARGET_MC, STOP, HOURS = 1e9, 5e6, 0.6, 4


def tape(path):
    cr, tg, buyers, creator_buy, mayhem, first = {}, {}, collections.defaultdict(set), collections.defaultdict(float), set(), {}
    for l in open(path):
        if '"t":"create"' in l:
            try: r = json.loads(l)
            except ValueError: continue
            cr[r['mint']] = r
        elif '"t":"trade"' in l:
            try: r = json.loads(l)
            except ValueError: continue
            m = r['mint']
            c = cr.get(m)
            if not c: continue
            if r['ts'] - c['ts'] <= 3600:
                if r['user'] == AGENT: mayhem.add(m)
                if r['side'] == 'buy':
                    if r['user'] == c['creator']: creator_buy[m] += r['sol']
                    elif m not in tg: buyers[m].add(r['user'])
            if r['v_sol'] >= 110 and m not in tg: tg[m] = r['ts']
    out = []
    for m, ts in tg.items():
        c = cr[m]
        out.append({'mint': m, 'create': c['ts'], 'grad': ts, 'name': c.get('name'), 'creator': c['creator'],
                    'mayhem': m in mayhem, 'selfgrad': creator_buy[m] >= 50, 'creator_sol': round(creator_buy[m], 2),
                    'buyers': len(buyers[m])})
    return out


def pool_of(mint):
    """Deepest GeckoTerminal pool for the mint (DexScreener has pruned the 10-14 coins)."""
    for attempt in range(3):
        try:
            d = http.get_json(f'{GT}/tokens/{mint}/pools', timeout=20)
            break
        except http.HttpError as e:
            if e.status != 429: raise
            time.sleep(30)
    else:
        return None
    ps = d.get('data') or []
    ps.sort(key=lambda p: -float((p.get('attributes') or {}).get('reserve_in_usd') or 0))
    return ps[0]['attributes']['address'] if ps else None


def candles(pool, start, end):
    path = (f'/pools/{pool}/ohlcv/minute?aggregate=1&before_timestamp={int(end)}&limit={int((end - start) / 60) + 2}'
            f'&currency=usd&token=base')
    for attempt in range(3):
        try:
            d = http.get_json(GT + path, timeout=20)
            rows = ((d.get('data') or {}).get('attributes') or {}).get('ohlcv_list') or []
            return sorted(r for r in rows if r[0] >= start - 60)
        except http.HttpError as e:
            if e.status != 429: raise
            time.sleep(30)
    return None


def fetch(limit):
    grads = json.load(open(GRADS))
    done = set()
    if CANDLES.exists():
        done = {json.loads(l)['mint'] for l in open(CANDLES)}
    n = 0
    with open(CANDLES, 'a') as f:
        for g in sorted(grads, key=lambda g: g['grad']):
            if g['mint'] in done: continue
            if limit and n >= limit: break
            n += 1
            row = {'mint': g['mint'], 'grad': g['grad']}
            try:
                pool = pool_of(g['mint'])
                row['pool'] = pool
                if pool:
                    time.sleep(3.0)
                    row['c'] = candles(pool, g['grad'], g['grad'] + HOURS * 3600)
            except Exception as e:                           # noqa: BLE001
                row['err'] = str(e)[:120]
            f.write(json.dumps(row) + '\n'); f.flush()
            time.sleep(3.0)
            if n % 20 == 0: print(n, 'fetched', flush=True)


def wilson(k, n, z=1.96):
    if not n: return (0, 0)
    p = k / n; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d; h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def path_stats(g, c):
    """-> dict(entry mc, max mult, stop-first?, ret under stop-or-sell-at-target) or None if unusable."""
    rows = [r for r in c if r[0] >= g['grad'] + 5]
    if len(rows) < 3: return None
    entry = rows[0][1]                                       # open of first full minute after grad+5s
    if not entry or entry <= 0 or entry * SUPPLY > 2e6 or entry * SUPPLY < 5e3: return None
    stop, hit_stop = entry * STOP, None
    mx = 0.0
    for r in rows:
        t, o, h, l, cl = r[:5]
        if l <= stop and hit_stop is None: hit_stop = t; break
        mx = max(mx, h / entry)
    allmx = max(r[2] for r in rows) / entry
    return {'entry_mc': entry * SUPPLY, 'max_mult': allmx, 'max_mult_prestop': mx, 'stopped': hit_stop is not None,
            'end_mult': rows[-1][4] / entry}


def score():
    grads = {g['mint']: g for g in json.load(open(GRADS))}
    rows, seen = [], set()
    for l in open(CANDLES):
        r = json.loads(l)
        if not r.get('c') or r['mint'] in seen: continue       # a second fetcher once appended duplicates
        seen.add(r['mint'])
        s = path_stats(grads[r['mint']], r['c'])
        if s: rows.append((grads[r['mint']], s))
    print(f"{len(grads)} graduates, {sum(1 for l in open(CANDLES))} fetched, {len(rows)} usable paths")

    def table(name, sel):
        xs = [s for g, s in rows if sel(g)]
        n = len(xs)
        if not n: print(f'{name:28s} n=0'); return
        out = [f'{name:28s} n={n:4d}']
        for k in (2, 10, 50, 100):
            h = sum(1 for s in xs if s['max_mult'] >= k)
            lo, hi = wilson(h, n)
            out.append(f'>= {k}x {h / n:5.1%} [{lo:4.1%},{hi:5.1%}]')
        # strategy: stop at -40%, else sell at 100x; unresolved at 4h -> end_mult
        ret = []
        for s in xs:
            if s['max_mult_prestop'] >= 100: ret.append(99.0)
            elif s['stopped']: ret.append(-0.40)
            else: ret.append(s['end_mult'] - 1)
        out.append(f'EV stop-40/sell-100x {sum(ret) / n:+.1%}')
        ret2 = [(99.0 if s['max_mult'] >= 100 else s['end_mult'] - 1) for s in xs]
        out.append(f'EV no-stop/sell-100x {sum(ret2) / n:+.1%}')
        print('  '.join(out))
    table('all graduates', lambda g: True)
    table('drop mayhem', lambda g: not g['mayhem'])
    table('drop mayhem+selfgrad', lambda g: not g['mayhem'] and not g['selfgrad'])
    table('organic only, buyers>=20', lambda g: not g['mayhem'] and not g['selfgrad'] and g['buyers'] >= 20)
    table('self-grad only', lambda g: g['selfgrad'])
    print('\ntop paths (max mult, entry mc, name):')
    for g, s in sorted(rows, key=lambda x: -x[1]['max_mult'])[:12]:
        print(f"  {s['max_mult']:7.1f}x  entry ${s['entry_mc']:,.0f}  {g['name']!r}  selfgrad={g['selfgrad']} mayhem={g['mayhem']} buyers={g['buyers']}")


if __name__ == '__main__':
    cmd = sys.argv[1]
    DATA.mkdir(exist_ok=True)
    if cmd == 'tape':
        gs = tape(sys.argv[2] if len(sys.argv) > 2 else '/Users/kapil/tapes/narr-tape-20261014T092453Z.jsonl')
        json.dump(gs, open(GRADS, 'w')); print(len(gs), 'graduates')
    elif cmd == 'fetch':
        lim = int(sys.argv[sys.argv.index('--limit') + 1]) if '--limit' in sys.argv else 0
        fetch(lim)
    elif cmd == 'score':
        score()
