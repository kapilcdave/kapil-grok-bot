"""Peak mc (1-min GT candles from pool creation, 8h) for single-name coins in recent_pools.json -> data/week/recent_peak.json"""
import json, sys, time, collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent)); sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import grad50
d = json.load(open('research/data/week/recent_pools.json'))
nm = lambda x: x['name'].split(' / ')[0]
cnt = collections.Counter(nm(x).lower() for x in d)
out = []
for x in d:
    if cnt[nm(x).lower()] != 1: continue
    try:
        c = grad50.candles(x['pool'], x['created'], x['created'] + 8 * 3600) or []
    except Exception as e: c = []
    pk = max((r[2] for r in c), default=0) * 1e9
    out.append({**x, 'name': nm(x), 'peak': pk, 'n': len(c)}); print(nm(x), round(pk/1e6, 2), len(c), flush=True); time.sleep(3)
json.dump(out, open('research/data/week/recent_peak.json', 'w'))
