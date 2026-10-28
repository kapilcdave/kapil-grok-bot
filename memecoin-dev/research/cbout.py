"""Outcomes for the frozen cbrule candidates: GT 1-min path 8h from create. python3 research/cbout.py fetch | score"""
import json, sys, time, math
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent)); sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import grad50, cbrule
OUT = Path('research/data/cbout.jsonl'); SUP = 1e9
def fetch():
    cs = [o for o in json.load(open('research/data/cbrule_all.json')) if cbrule.rule(o)]
    done = {json.loads(l)['mint'] for l in open(OUT)} if OUT.exists() else set()
    with open(OUT, 'a') as f:
        for i, o in enumerate(sorted(cs, key=lambda o: o['create'])):
            if o['mint'] in done: continue
            row = dict(o)
            try:
                p = grad50.pool_of(o['mint']); row['pool'] = p; time.sleep(3)
                if p: row['c'] = grad50.candles(p, o['create'], o['create'] + 8 * 3600)
            except Exception as e: row['err'] = str(e)[:100]
            f.write(json.dumps(row) + '\n'); f.flush(); time.sleep(3)
            if i % 20 == 0: print(i, len(cs), flush=True)
if __name__ == '__main__':
    fetch() if sys.argv[1] == 'fetch' else None
