"""Who bought in the create block (and the next few) of each runner: from getBlock, not from the swept tapes.

For a coin that graduated at launch the pool does not exist before the create block, so the first blocks hold
every participant. Find the create slot through the dev wallet's own signatures (a few per fresh dev), fetch that
block and the next `--blocks`, keep every tx that touches the mint. Per tx: signer, side, SOL, tokens, fee/priority,
Jito tip, programs. Output research/data/week/createblock.jsonl (resumable).

  python3 research/createblock.py stage2.json [--only NAME] [--limit N] [--blocks 8]
"""
import argparse, json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from chaintrades import Rpc                                # noqa: E402

OUT = Path(__file__).resolve().parent / 'data' / 'week' / (__import__('os').environ.get('CB_OUT') or 'createblock.jsonl')
TIPS = {'96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5', 'HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe',
        'Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY', 'ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49',
        'DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh', 'ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt',
        'DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL', '3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT'}
BLOCK = {'transactionDetails': 'full', 'rewards': False, 'maxSupportedTransactionVersion': 1, 'encoding': 'json'}


def keys_of(tx):
    m = tx['transaction']['message']
    ks = list(m['accountKeys'])
    la = (tx['meta'] or {}).get('loadedAddresses') or {}
    return ks + la.get('writable', []) + la.get('readonly', []), m


def get_block(rpc, slot):
    try:
        return rpc('getBlock', [slot, BLOCK])
    except RuntimeError:
        return None


def touches(blk, mint):
    out = []
    for i, tx in enumerate((blk or {}).get('transactions') or []):
        ks, _ = keys_of(tx)
        if mint in ks and not (tx['meta'] or {}).get('err'):
            out.append((i, tx, ks))
    return out


def record(i, tx, ks, mint, slot, slot0):
    m, meta = tx['transaction']['message'], tx['meta']
    signer = ks[0]
    pre = {(b['owner'], b['mint']): float(b['uiTokenAmount']['uiAmountString'] or 0) for b in meta.get('preTokenBalances') or []}
    post = {(b['owner'], b['mint']): float(b['uiTokenAmount']['uiAmountString'] or 0) for b in meta.get('postTokenBalances') or []}
    dtok = post.get((signer, mint), 0) - pre.get((signer, mint), 0)
    dsol = (meta['postBalances'][0] - meta['preBalances'][0]) / 1e9
    tip = sum((meta['postBalances'][j] - meta['preBalances'][j]) for j, k in enumerate(ks) if k in TIPS) / 1e9
    fee = meta['fee'] / 1e9
    progs = sorted({ks[ins['programIdIndex']] for ins in m['instructions']})
    return {'i': i, 'dslot': slot - slot0, 'sig': tx['transaction']['signatures'][0], 'signer': signer, 'dtok': dtok,
            'dsol': round(dsol, 6), 'fee': fee, 'tip': tip, 'progs': progs, 'n_ix': len(m['instructions'])}


def find_create(rpc, mint, dev, created, back=1800):
    """Earliest dev tx in [created-back, created+40] that touches the mint (Jupiter's createdAt is the first pool's
    time, which can trail the real create by up to minutes)."""
    before, tries, cand = None, 0, []
    while tries < 3:
        page = rpc('getSignaturesForAddress', [dev, {'limit': 1000, **({'before': before} if before else {})}]) or []
        cand += [s for s in page if not s.get('err') and s.get('blockTime') and created - back <= s['blockTime'] <= created + 40]
        if len(page) < 1000 or (page[-1].get('blockTime') or 0) < created - back: break
        before, tries = page[-1]['signature'], tries + 1
    for s in sorted(cand, key=lambda s: (s['slot']))[:40]:
        try:
            tx = rpc('getTransaction', [s['signature'], {'encoding': 'json', 'maxSupportedTransactionVersion': 1, 'commitment': 'confirmed'}])
        except RuntimeError:
            continue
        if tx and mint in keys_of(tx)[0]:
            return s['slot']
    return None


def one(rpc, c, nblocks):
    slot0 = find_create(rpc, c['mint'], c['dev'], c['created'])
    if not slot0: return {'mint': c['mint'], 'name': c['name'], 'err': 'create slot not found'}
    txs = []
    for slot in range(slot0, slot0 + nblocks + 1):
        blk = get_block(rpc, slot)
        if not blk: continue
        for i, tx, ks in touches(blk, c['mint']):
            txs.append(record(i, tx, ks, c['mint'], slot, slot0))
    return {'mint': c['mint'], 'name': c['name'], 'dev': c['dev'], 'created': c['created'], 'slot0': slot0, 'txs': txs}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('stage2')
    ap.add_argument('--only'); ap.add_argument('--limit', type=int, default=0); ap.add_argument('--blocks', type=int, default=8)
    ap.add_argument('--min-peak', type=float, default=1e6)
    a = ap.parse_args()
    cs = [c for c in json.load(open(a.stage2)) if c['dev'] and c['peak'] >= a.min_peak and (__import__('os').environ.get('CB_OUT') or c['peak'] < 1e8)]
    if a.only: cs = [c for c in cs if c['name'] == a.only]
    done = {json.loads(l)['mint'] for l in open(OUT)} if OUT.exists() else set()
    rpc, n = Rpc(), 0
    with open(OUT, 'a') as f:
        for c in sorted(cs, key=lambda c: c['created']):
            if c['mint'] in done: continue
            if a.limit and n >= a.limit: break
            n += 1
            try: r = one(rpc, c, a.blocks)
            except Exception as e:                          # noqa: BLE001
                r = {'mint': c['mint'], 'name': c['name'], 'err': str(e)[:150]}
            f.write(json.dumps(r) + '\n'); f.flush()
            print(n, c['name'], r.get('err') or f"{len(r['txs'])} txs slot0={r['slot0']}", 'calls', rpc.calls, flush=True)


if __name__ == '__main__':
    main()
