"""Candidates for PREREG-cbrule-20261027: count only (no outcomes). python3 research/cbrule.py cand [tape]"""
import json, sys, collections
AGENT = 'BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s'
BR = {'spacex','nvidia','mrbeast','tesla si','truth','x','chatgpt','gta 6','gta 6 coin','gta6','musk','barron','spacexsi','doge','btc','mercury','benz','mcnugget','mcsauce','mcribinu','mcrib','elon','tiktok','googl','google','tesla','openai','apple','trump','privacy'}
W = 2
def cand(path):
    cr, buy, dev, mayhem = {}, collections.defaultdict(lambda: collections.defaultdict(float)), collections.defaultdict(float), set()
    for l in open(path):
        if '"t":"create"' in l:
            try: r = json.loads(l)
            except ValueError: continue
            cr[r['mint']] = r
        elif '"t":"trade"' in l:
            try: r = json.loads(l)
            except ValueError: continue
            c = cr.get(r['mint'])
            if not c: continue
            if r['user'] == AGENT and r['ts'] - c['ts'] <= 3600: mayhem.add(r['mint'])
            if r['side'] == 'buy' and r['slot'] - c['slot'] <= W:
                if r['user'] == c['creator']: dev[r['mint']] += r['sol']
                else: buy[r['mint']][r['user']] += r['sol']
    out = []
    for m, c in cr.items():
        b = buy.get(m, {})
        out.append({'mint': m, 'name': c.get('name'), 'create': c['ts'], 'dev': round(dev[m], 2), 'nsol': round(sum(b.values()), 2),
                    'nbuy': len(b), 'mayhem': m in mayhem})
    return out
def rule(o, brand=True):
    return (o['dev'] < 2 and o['nsol'] >= 20 and o['nbuy'] >= 3 and not o['mayhem'] and (not brand or (o['name'] or '').lower() not in BR))
if __name__ == '__main__':
    p = sys.argv[2] if len(sys.argv) > 2 else '/Users/kapil/tapes/narr-tape-20261014T092453Z.jsonl'
    os = cand(p)
    json.dump(os, open('research/data/cbrule_all.json', 'w'))
    print(len(os), 'launches;', sum(not o['mayhem'] for o in os), 'non-mayhem')
    print('rule A (brand dropped):', sum(rule(o) for o in os), ' rule B (brands kept):', sum(rule(o, False) for o in os))
