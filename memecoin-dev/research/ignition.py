"""Who buys while a real runner breaks out (150k -> 1M mc)? Sample 5 blocks across that window, tag signers vs kol_registry.json,
Jito tips, size. python3 research/ignition.py -> research/data/week/ignition.jsonl"""
import json, sys
from pathlib import Path
sys.path.insert(0, 'research'); sys.path.insert(0, '.')
from chaintrades import Rpc
from createblock import BLOCK, keys_of, TIPS
KOL = set(json.load(open('../crypto/dexarb/kolcopy/kol_registry.json')))
S2 = {c['name']: c for c in json.load(open('research/data/week/stage2.json'))}
CB = {r['name']: r for r in map(json.loads, open('research/data/week/createblock.jsonl')) if r.get('txs')}
OUT = Path('research/data/week/ignition.jsonl')
NAMES = ['OA', 'ODIE', 'BITCHES', 'YMBCOIN', 'KIMCHINU', 'SHAVCOIN', 'KIMCHIBULL', 'Pokecity', 'MONEYBIZ']

def blk(rpc, s):
    try: return rpc('getBlock', [s, BLOCK])
    except RuntimeError: return None

def slot_at(rpc, slot0, t0, T):
    s = slot0 + int((T - t0) / 0.4)
    for _ in range(3):
        b = None
        for d in (0, 1, 2, 3):
            b = blk(rpc, s + d)
            if b and b.get('blockTime'): s += d; break
        if not b: return None, None
        err = T - b['blockTime']
        if abs(err) <= 3: return s, b
        s += int(err / 0.4)
    return s, blk(rpc, s)

def main():
    done = {json.loads(l)['name'] for l in open(OUT)} if OUT.exists() else set()
    rpc = Rpc()
    with open(OUT, 'a') as f:
        for n in NAMES:
            if n in done or n not in S2 or n not in CB: continue
            c, cb = S2[n], CB[n]; x = c['x']
            a, b = x.get('150000.0'), x.get('1000000.0')
            if not a or not b or b <= a: continue
            t0 = c['created']; row = {'name': n, 'mint': c['mint'], 't150': a, 't1m': b, 'min_to_1m': round((b - a) / 60, 1), 'samples': []}
            for T in [a + (b - a) * k / 4 for k in range(5)]:
                s, bl = slot_at(rpc, cb['slot0'], t0, int(T))
                if not bl: continue
                buys = []
                for tx in bl['transactions']:
                    ks, m = keys_of(tx)
                    if c['mint'] not in ks or (tx['meta'] or {}).get('err'): continue
                    meta, sg = tx['meta'], ks[0]
                    pre = {(q['owner'], q['mint']): float(q['uiTokenAmount']['uiAmountString'] or 0) for q in meta.get('preTokenBalances') or []}
                    post = {(q['owner'], q['mint']): float(q['uiTokenAmount']['uiAmountString'] or 0) for q in meta.get('postTokenBalances') or []}
                    dt = post.get((sg, c['mint']), 0) - pre.get((sg, c['mint']), 0); ds = (meta['postBalances'][0] - meta['preBalances'][0]) / 1e9
                    tip = sum(meta['postBalances'][j] - meta['preBalances'][j] for j, k in enumerate(ks) if k in TIPS) / 1e9
                    buys.append({'w': sg, 'side': 'b' if dt > 0 else 's' if dt < 0 else 'x', 'sol': round(-ds, 2), 'tip': tip, 'kol': sg in KOL, 'prog': len(m['instructions'])})
                row['samples'].append({'T': int(T), 'slot': s, 'off_s': int(T) - t0, 'n': len(buys), 'buys': buys})
            f.write(json.dumps(row) + '\n'); f.flush(); print(n, rpc.calls, flush=True)
main()
