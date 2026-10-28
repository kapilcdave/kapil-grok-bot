import json, sys, time
from pathlib import Path
sys.path.insert(0, 'research'); sys.path.insert(0, '.')
import grad50
OUT = Path('research/data/cbtmpl_out.jsonl')
cs = json.load(open('research/data/cbtmpl_cands.json'))
done = {json.loads(l)['mint'] for l in open(OUT)} if OUT.exists() else set()
with open(OUT, 'a') as f:
    for o in cs:
        if o['mint'] in done: continue
        row = dict(o)
        try:
            p = grad50.pool_of(o['mint']); row['pool'] = p; time.sleep(3)
            if p: row['c'] = grad50.candles(p, o['create'], o['create'] + 8 * 3600)
            else: row['c'] = []
        except Exception as e: row['err'] = str(e)[:100]
        f.write(json.dumps(row) + '\n'); f.flush(); time.sleep(3)
