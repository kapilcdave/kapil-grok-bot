import json, os, statistics, time, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sellsim
QUOTE={"wbnb":"0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",
       "usdt":"0x55d398326f99059ff775485246999027b3197955",
       "busd":"0xe9e7cea3dedca5984780bafc599bd69add087d56",
       "usdc":"0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d"}
QS=set(QUOTE.values())

rows=json.load(open("data/bsc_pancake_open_tape.json"))
if os.path.exists("data/pancake_bulk.jsonl"):
    for ln in open("data/pancake_bulk.jsonl"):
        try: rows.append(json.loads(ln))
        except Exception: pass
seen=set(); uniq=[]
for r in rows:
    if r["pair"] in seen: continue
    seen.add(r["pair"]); uniq.append(r)
live=[r for r in uniq if r["mints"] and r["swaps"] and r["qsym"]=="WBNB"]
print(f"WBNB launches with liquidity+trades: {len(live)}",file=sys.stderr)

res=[]
for i,r in enumerate(live):
    try:
        t0,t1=sellsim.pair_tokens(r["pair"]) if hasattr(sellsim,"pair_tokens") else (None,None)
    except Exception:
        t0=t1=None
    if t0 is None:
        try:
            t0="0x"+sellsim.rpc("eth_call",[{"to":r["pair"],"data":sellsim.SEL_T0},"latest"])[-40:]
            t1="0x"+sellsim.rpc("eth_call",[{"to":r["pair"],"data":sellsim.SEL_T1},"latest"])[-40:]
        except Exception as e:
            continue
    tok = t1 if t0.lower() in QS else t0
    if tok.lower() in QS: continue
    # realized outcome from the tape
    devtx=r["mints"][0]["tx"]
    seat=next((s for s in r["swaps"] if s["px"] and s["tx"]!=devtx and s["buy"]),None)
    if not seat: continue
    after=[s for s in r["swaps"] if s["px"] and (s["blk"],s["txi"])>(seat["blk"],seat["txi"])]
    sells=[s for s in after if not s["buy"]]
    realized_stuck = len(sells)==0
    sim=sellsim.check(tok,0.01)
    res.append(dict(pair=r["pair"],token=tok,stuck=realized_stuck,
                    verdict=sim["verdict"],recovery=sim.get("recovery"),
                    n_sells=len(sells),err=sim.get("err","")))
    print(f"  [{i+1}/{len(live)}] {tok[:12]} realized={'STUCK' if realized_stuck else 'sold(%d)'%len(sells):9s} "
          f"sim={sim['verdict']:16s} rec={sim.get('recovery')}",file=sys.stderr)
    time.sleep(0.4)
json.dump(res,open("data/sim_validation.json","w"))

print("\n=== CONFUSION MATRIX: simulator verdict vs realized outcome ===")
SAFE=lambda x: x["verdict"]=="SELLABLE" and (x["recovery"] or 0)>=0.80
tp=sum(1 for x in res if x["stuck"] and not SAFE(x))
fn=sum(1 for x in res if x["stuck"] and SAFE(x))
fp=sum(1 for x in res if not x["stuck"] and not SAFE(x))
tn=sum(1 for x in res if not x["stuck"] and SAFE(x))
print(f"  n={len(res)}   filter = SELLABLE and recovery>=0.80")
print(f"    realized STUCK, simulator REJECTED  (saved)   : {tp}")
print(f"    realized STUCK, simulator PASSED    (missed)  : {fn}")
print(f"    realized SOLD , simulator REJECTED  (foregone): {fp}")
print(f"    realized SOLD , simulator PASSED    (kept)    : {tn}")
if tp+fn: print(f"    recall on stuck positions: {100*tp/(tp+fn):.0f}%")
if tp+fp: print(f"    precision of rejection   : {100*tp/(tp+fp):.0f}%")
print("\n  verdict breakdown:")
import collections
for k,v in collections.Counter(x["verdict"] for x in res).most_common(): print(f"    {k:18s} {v}")
rr=[x["recovery"] for x in res if x.get("recovery") is not None]
if rr: print(f"\n  recovery among SELLABLE: n={len(rr)} median={statistics.median(rr):.4f} min={min(rr):.4f} max={max(rr):.4f}")
