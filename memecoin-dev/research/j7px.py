"""Track the existing coins j7 matches to a tweet: DexScreener mc at the tweet and at fixed offsets after it.

For every non-reply tweet with an `ai_suggestion_update`, each matched mint is priced at tweet-arrival + OFFSETS.
The last two offsets are a PLACEBO window for the same coin an hour later (no news), so the news-window return can be
compared with the coin's own drift. Raw rows only; nothing is decided here. Scored by `j7px_score.py`.

    python3 -m research.j7px --j7 research/data/j7/j7tape-20261024.jsonl --out ~/tapes/j7px-20261024.jsonl --hours 6
"""
import argparse, heapq, json, os, re, time, urllib.request

OFFSETS = (0, 30, 60, 120, 300, 600, 1800, 3600, 4200)    # 3600 -> 4200 is the placebo window
MINT = re.compile(r'/([1-9A-HJ-NP-Za-km-z]{32,44})\.webp')
API = 'https://api.dexscreener.com/tokens/v1/solana/'


def fetch(mints):
    out = {}
    for i in range(0, len(mints), 30):
        chunk = mints[i:i + 30]
        try:
            req = urllib.request.Request(API + ','.join(chunk), headers={'User-Agent': 'Mozilla/5.0'})
            d = json.load(urllib.request.urlopen(req, timeout=15))
        except Exception:
            continue
        for p in d:
            m = p['baseToken']['address']
            liq = (p.get('liquidity') or {}).get('usd') or 0
            if m not in out or liq > out[m].get('liq', 0):
                out[m] = dict(mc=p.get('marketCap') or p.get('fdv'), px=p.get('priceUsd'), liq=liq, dex=p.get('dexId'),
                              pair=p.get('pairAddress'), created=(p.get('pairCreatedAt') or 0) / 1000)
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--j7', required=True); ap.add_argument('--out', required=True)
    ap.add_argument('--hours', type=float, default=6); a = ap.parse_args()
    end = time.time() + a.hours * 3600
    types, heap, seq, seen = {}, [], 0, set()
    out = open(os.path.expanduser(a.out), 'a', buffering=1)
    f = open(a.j7); f.seek(0, 2); f.seek(max(0, f.tell() - 400000)); f.readline()    # start ~1 min back, on a line boundary
    while time.time() < end:
        while True:
            pos = f.tell(); l = f.readline()
            if not l: break
            try: r = json.loads(l)
            except ValueError: f.seek(pos); break          # partial line at the end: re-read next pass
            e, d = r['event'], r['data']; rn = r['recv_ns'] / 1e9
            if e == 'tweet': types[d['id']] = d.get('type')
            elif e == 'ai_suggestion_update' and types.get(d['tweet_id']) in ('TWEET', 'QUOTE') and time.time() - rn < 120:
                mints = list(dict.fromkeys(m.group(1) for x in d['results'] if (m := MINT.search(x.get('image') or ''))))
                for m in mints:
                    if (d['tweet_id'], m) in seen: continue
                    seen.add((d['tweet_id'], m))
                    for k in OFFSETS:
                        seq += 1; heapq.heappush(heap, (rn + k, seq, d['tweet_id'], m, k))
        now = time.time(); due = []
        while heap and heap[0][0] <= now: due.append(heapq.heappop(heap))
        if due:
            px = fetch(list({x[3] for x in due})); ts = time.time()
            for _, _, tid, m, k in due:
                out.write(json.dumps(dict(t='px', ts=ts, tweet=tid, mint=m, k=k, **(px.get(m) or dict(mc=None)))) + '\n')
        time.sleep(1.0)


if __name__ == '__main__':
    main()
