"""Post-launch buyer timeline: sample blocks at fixed slot offsets after the create slot, for template runners vs same-shape duds.
python3 research/postbuyers.py   -> research/data/week/postbuyers.jsonl (resumable)"""
import json, sys, hashlib, random
from pathlib import Path
sys.path.insert(0, 'research'); sys.path.insert(0, '.')
from chaintrades import Rpc
from createblock import BLOCK, keys_of, TIPS
OFFS = [5, 15, 40, 100, 250, 600, 1400]
OUT = Path('research/data/week/postbuyers.jsonl')
DUST = ('27HFmP7c', 'Fs9RN3wA', '7NNzakXL')
RUN = {'OA','ODIE','BITCHES','YMBCOIN','KIMCHINU','SHAVCOIN','KIMCHIBULL','Pokecity','MONEYBIZ','CATTLEAXE','███WIF'}

def groups():
    cb = [json.loads(l) for f in ('createblock.jsonl', 'createblock_recent.jsonl') for l in open('research/data/week/' + f)]
    runners = [{'mint': r['mint'], 'name': r['name'], 'slot0': r['slot0'], 'g': 'run'} for r in cb if r.get('txs') and r['name'] in RUN]
    out = {json.loads(l)['mint']: json.loads(l) for l in open('research/data/cbout.jsonl')}
    pk = lambda r: max((c[2] for c in r.get('c') or []), default=0) * 1e9
    pool = [r for r in out.values() if 18 <= r['nsol'] < 50 and r['dev'] < 2 and r['nbuy'] >= 3 and pk(r) < 2.5e5]
    pool.sort(key=lambda r: hashlib.md5(r['mint'].encode()).hexdigest())      # fixed pseudo-random order, no outcome peeking beyond the <250k filter
    want = {r['mint']: r for r in pool[:13]}
    for l in open('/Users/kapil/tapes/narr-tape-20261014T092453Z.jsonl'):
        if '"t":"create"' in l:
            try: c = json.loads(l)
            except ValueError: continue
            if c['mint'] in want: want[c['mint']]['slot0'] = c['slot']
    duds = [{'mint': m, 'name': r['name'], 'slot0': r['slot0'], 'g': 'dud'} for m, r in want.items() if 'slot0' in r]
    return runners + duds

def sample(rpc, c, off):
    try: blk = rpc('getBlock', [c['slot0'] + off, BLOCK])
    except RuntimeError: blk = None
    if not blk: return {'off': off, 'skipped': True}
    nb = ns = 0; sb = ss = 0.0; wb = set(); big = tip = dust = 0; ntx = 0
    for tx in blk['transactions']:
        ks, m = keys_of(tx)
        if c['mint'] not in ks or (tx['meta'] or {}).get('err'): continue
        ntx += 1; meta = tx['meta']; s = ks[0]
        pre = {(b['owner'], b['mint']): float(b['uiTokenAmount']['uiAmountString'] or 0) for b in meta.get('preTokenBalances') or []}
        post = {(b['owner'], b['mint']): float(b['uiTokenAmount']['uiAmountString'] or 0) for b in meta.get('postTokenBalances') or []}
        dt = post.get((s, c['mint']), 0) - pre.get((s, c['mint']), 0); ds = (meta['postBalances'][0] - meta['preBalances'][0]) / 1e9
        if dt > 0:
            nb += 1; sb += -ds; wb.add(s); big += -ds >= 1; dust += s[:8] in DUST
            tip += sum(meta['postBalances'][j] - meta['preBalances'][j] for j, k in enumerate(ks) if k in TIPS) > 0
        elif dt < 0: ns += 1; ss += ds
    return {'off': off, 'ntx': ntx, 'nbuy': nb, 'nsell': ns, 'sol_buy': round(sb, 2), 'sol_sell': round(ss, 2), 'uniq_buyers': len(wb), 'big_buys': big, 'tipped_buys': tip, 'dust_buys': dust}

def main():
    done = {json.loads(l)['mint'] for l in open(OUT)} if OUT.exists() else set()
    rpc = Rpc()
    with open(OUT, 'a') as f:
        for c in groups():
            if c['mint'] in done: continue
            row = {**c, 'samples': [sample(rpc, c, o) for o in OFFS]}
            f.write(json.dumps(row) + '\n'); f.flush(); print(c['g'], c['name'], 'calls', rpc.calls, flush=True)
if __name__ == '__main__':
    main()
