"""Create-block features for every graduate on the 10-14 tape (same-day negatives for the week's runners)."""
import json, collections, sys
sys.path.insert(0, 'research')
T = '/Users/kapil/tapes/narr-tape-20261014T092453Z.jsonl'
grads = {g['mint']: g for g in json.load(open('research/data/grad50_grads.json'))}
cr, tr = {}, collections.defaultdict(list)
for l in open(T):
    if '"t":"create"' in l:
        try: r = json.loads(l)
        except ValueError: continue
        if r['mint'] in grads: cr[r['mint']] = r
    elif '"t":"trade"' in l:
        m = l[l.index('"mint":"') + 8:][:44]
        if m in grads:
            try: tr[m].append(json.loads(l))
            except ValueError: pass
out = {}
for m, g in grads.items():
    c = cr.get(m)
    if not c: continue
    s0, ts = c['slot'], sorted(tr[m], key=lambda r: (r['slot'], r['recv']))
    dev = c['creator']
    f = collections.defaultdict(float); buyers = collections.defaultdict(lambda: collections.defaultdict(float))
    for r in ts:
        d = r['slot'] - s0
        if d > 8 or r['side'] != 'buy': continue
        if r['user'] == dev: f['dev_sol'] += r['sol']; f['dev_sol_s0'] += r['sol'] if d == 0 else 0
        else: buyers[min(d, 3)][r['user']] += r['sol']
    allb = collections.defaultdict(float)
    for d in buyers:
        for u, s in buyers[d].items(): allb[u] += s
    out[m] = dict(name=g['name'], mayhem=g['mayhem'], selfgrad=g['selfgrad'], dev_sol=round(f['dev_sol'], 2), dev_sol_s0=round(f['dev_sol_s0'], 2),
                  nb0=len(buyers[0]), sol0=round(sum(buyers[0].values()), 2), nb1=len(buyers[1]), nb2=len(buyers[2]), nb3p=len(buyers[3]),
                  nb_all=len(allb), sol_all=round(sum(allb.values()), 2), top_share=round(max(allb.values()) / sum(allb.values()), 3) if allb else None,
                  grad_slot_lag=(ts[0]['slot'] - s0) if ts else None, grad=g['grad'] - g['create'])
json.dump(out, open('research/data/grad50_feat.json', 'w'))
print(len(out), 'graduates with features')
