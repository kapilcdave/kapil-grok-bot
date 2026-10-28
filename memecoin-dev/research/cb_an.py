import json, collections, statistics as st
rows = [json.loads(l) for l in open('research/data/week/createblock.jsonl')]
ok = [r for r in rows if r.get('txs') is not None]
print(len(rows), 'rows;', len(ok), 'with create block;', collections.Counter(r.get('err') for r in rows if r.get('err')).most_common(3))
S2 = {o['mint']: o for o in json.load(open('research/data/week/stage2.json'))}
feat = {}
for r in ok:
    dev = r['dev']; f = collections.defaultdict(float); b = collections.defaultdict(lambda: collections.defaultdict(float)); tips = 0; fees = []
    for t in r['txs']:
        if t['dtok'] > 0:
            if t['signer'] == dev: f['dev_sol'] += -t['dsol']; f['dev_tok'] += t['dtok']
            else: b[min(t['dslot'], 3)][t['signer']] += -t['dsol']
        if t['signer'] != dev: tips += t['tip'] > 0; fees.append(t['fee'])
    allb = collections.defaultdict(float)
    for d in b:
        for u, s in b[d].items(): allb[u] += s
    feat[r['mint']] = dict(name=r['name'], dev_sol=round(f['dev_sol'], 1), dev_pct=round(f['dev_tok'] / 1e7, 1), nb0=len(b[0]), sol0=round(sum(b[0].values()), 1),
                           nb1=len(b[1]), nb2=len(b[2]), nb3p=len(b[3]), nb_all=len(allb), sol_all=round(sum(allb.values()), 1),
                           tipped=tips, ntx=len(r['txs']), peak=S2[r['mint']]['peak'])
json.dump(feat, open('research/data/week/cb_feat.json', 'w'))
def q(xs):
    xs = sorted(xs); n = len(xs)
    return f"p10 {xs[n//10]:.1f} p25 {xs[n//4]:.1f} med {xs[n//2]:.1f} p75 {xs[3*n//4]:.1f} p90 {xs[9*n//10]:.1f}" if n else 'n=0'
for k in ('dev_sol', 'dev_pct', 'nb0', 'sol0', 'nb1', 'nb_all', 'sol_all', 'tipped'):
    print(f"RUNNERS  {k:8s} {q([v[k] for v in feat.values()])}")
neg = json.load(open('research/data/grad50_feat.json'))
for k in ('dev_sol', 'nb0', 'sol0', 'nb1', 'nb_all', 'sol_all'):
    print(f"10-14 ALL GRADS {k:8s} {q([v[k] for v in neg.values()])}")
