"""Stage 2: per-coin timeline from stage1.jsonl -> mc crossings, peak, launchpad, dev."""
import json, sys, time, collections, statistics
rows = [json.loads(l) for l in open('research/data/week/stage1.jsonl')]
hm = lambda t: time.strftime('%m-%d %H:%M', time.gmtime(t)) if t else '-'
out = []
for r in rows:
    lc, c = r.get('launch') or {}, r.get('c') or []
    if not lc.get('created') or len(c) < 5: continue
    sup = lc.get('supply') or 1e9
    # clean wick prints: cap high at 1.5x max(open, close, next close)
    k = []
    for i, x in enumerate(c):
        ref = max(x[1], x[4], c[i + 1][4] if i + 1 < len(c) else x[4]); k.append((x[0], x[1] * sup, min(x[2], 1.5 * ref) * sup, x[3] * sup, x[4] * sup))
    pk = max(range(len(k)), key=lambda i: k[i][2])
    def cross(th):
        return next((x[0] for x in k if x[2] >= th), None)
    d = dict(name=r['name'], mint=r['mint'], pad=lc.get('launchpad'), created=lc['created'], grad=lc.get('graduated'),
             first=k[0][0], mc0=k[0][1], peak=k[pk][2], tpk=k[pk][0], dev=lc.get('dev'), tw=lc.get('twitter'),
             x={th: cross(th) for th in (5e4, 1.5e5, 5e5, 1e6, 5e6)}, n=len(k), tape=r['tape'])
    out.append(d)
json.dump(out, open('research/data/week/stage2.json', 'w'))
print(len(rows), 'rows;', len(out), 'with paths')
print('launchpad:', collections.Counter(o['pad'] for o in out).most_common())
real = [o for o in out if o['peak'] >= 1e6 and o['peak'] / max(o['mc0'], 1) >= 5]
print(len(real), 'with peak >= $1M and >= 5x from first candle')
by = collections.Counter(time.strftime('%m-%d', time.gmtime(o['created'])) for o in real); print(sorted(by.items()))
def q(xs):
    xs = sorted(xs); return f"n={len(xs)} p25 {xs[len(xs)//4]:.0f} med {xs[len(xs)//2]:.0f} p75 {xs[3*len(xs)//4]:.0f}" if xs else 'n=0'
for a, b, nm in ((None, 5e4, 'create->50k'), (5e4, 1.5e5, '50k->150k'), (1.5e5, 1e6, '150k->1M'), (1e6, 5e6, '1M->5M')):
    xs = []
    for o in real:
        ta = o['created'] if a is None else o['x'][a]; tb = o['x'][b]
        if ta and tb: xs.append((tb - ta) / 60)
    print(f'{nm:12s} min: {q(xs)}')
print('minutes create -> peak:', q([(o['tpk'] - o['created']) / 60 for o in real]))
print('minutes create -> first candle:', q([(o['first'] - o['created']) / 60 for o in real]))
print('graduated:', sum(1 for o in real if o['grad']), '/', len(real), '; grad lag s', q([o['grad'] - o['created'] for o in real if o['grad']]))
print('first-candle mc:', q([o['mc0'] for o in real]))
print('peak mc (M):', q([o['peak'] / 1e6 * 100 for o in real]), '(x100)')
print('\nTOP 40 by peak (created, pad, first mc, 50k, 150k, 1M, peak, min to peak, tape post?):')
for o in sorted(real, key=lambda o: -o['peak'])[:40]:
    f = lambda t: f"{(t - o['created'])/60:5.0f}m" if t else '    - '
    print(f"{o['name'][:12]:<12} {hm(o['created'])} {str(o['pad'])[:8]:<8} mc0 {o['mc0']/1e3:6.0f}k  50k {f(o['x'][5e4])} 150k {f(o['x'][1.5e5])} 1M {f(o['x'][1e6])} 5M {f(o['x'][5e6])}  peak {o['peak']/1e6:6.2f}M @ {(o['tpk']-o['created'])/60:5.0f}m  post={'Y' if o['tape'].get('post') else '-'}")
