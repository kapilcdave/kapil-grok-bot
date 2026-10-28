"""Week of runners (created 10-20..10-24, peak >= $1M on the recorder): launch facts + full 8 h path from the coin's own birth.

Operator 10-27: "pull the last week's daily runners, deep research: how launched, timeline, KOL/j7, news, X post, how fast
after the post did they run". Stage 1 (this file): Jupiter launch facts, GeckoTerminal 1-min path (curve pool then graduated
pool) for 8 h from creation, tape-side post/link fields. Output: research/data/week/stage1.jsonl (resumable).

  python3 research/weekrunners.py <seen_week.json>
"""
import json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from devbot import http                                    # noqa: E402
from runners import features, sources                      # noqa: E402

OUT = Path(__file__).resolve().parent / 'data' / 'week'
OUT.mkdir(parents=True, exist_ok=True)
T0 = 1792454400                                            # 2026-10-20 00:00Z
HOURS = 8


def path(lc):
    created, grad = lc['created'], lc.get('graduated') or 1e18
    gp = lc.get('grad_pool')
    try:
        cp = features.curve_pool(lc['mint'], gp)
    except Exception:                                      # noqa: BLE001
        cp = lc.get('first_pool')
    pools = [p for p in dict.fromkeys((cp, gp)) if p]
    rows = {}
    for p in pools:
        curve = p != gp
        try:
            for r in features._candles(p, created, created + HOURS * 3600):
                if (r[0] < grad) == curve or len(pools) == 1:
                    rows.setdefault(int(r[0]), r)
        except Exception:                                  # noqa: BLE001
            continue
    return pools, [rows[k] for k in sorted(rows)]


def main():
    seen = json.load(open(sys.argv[1]))
    cands = [x for x in seen if x.get('created') and x['created'] >= T0 and 1e6 <= x['peak'] < 2e8]
    f = OUT / 'stage1.jsonl'
    done = {json.loads(l)['mint'] for l in open(f)} if f.exists() else set()
    with open(f, 'a') as fh:
        for i, c in enumerate(sorted(cands, key=lambda x: x['created'])):
            if c['mint'] in done: continue
            row = {'mint': c['mint'], 'name': c['name'], 'tape': {k: c.get(k) for k in
                   ('path', 'created', 'post', 'post_url', 'admit', 'admit_mc', 'peak', 'tpk', 'dev_sol', 'pair', 'pool')}}
            try:
                lc = features.launch(features.jup_token(c['mint']))
                row['launch'] = lc
                if lc.get('created'):
                    row['pools'], row['c'] = path(lc)
            except Exception as e:                         # noqa: BLE001
                row['err'] = str(e)[:150]
            fh.write(json.dumps(row) + '\n'); fh.flush()
            if i % 10 == 0: print(i, len(cands), flush=True)


if __name__ == '__main__':
    main()
