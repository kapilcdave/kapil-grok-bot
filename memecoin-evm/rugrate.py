import json, os, sys, time, statistics, collections
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import sellsim as S
T_BURN="0xdccd412f0b1252819cb1fd330b93224ca42612892bb3f4f789976e6d81936496"
T_MINT="0x4c209b5fc8ad50758f13e2e1088ba56a560dff690a1c6fef26394f4c03821c4f"
QS={"0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"}

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
print(f"WBNB launches: {len(live)}",file=sys.stderr)

out=[]
for i,r in enumerate(live):
    rec={"pair":r["pair"]}
    try:
        rs=S.rpc("eth_call",[{"to":r["pair"],"data":"0x0902f1ac"},"latest"])[2:]
        r0,r1=int(rs[0:64],16),int(rs[64:128],16)
        t0="0x"+S.rpc("eth_call",[{"to":r["pair"],"data":S.SEL_T0},"latest"])[-40:]
        rq = r0 if t0.lower() in QS else r1
        rec["wbnb_now"]=rq/1e18
    except Exception as e:
        rec["wbnb_now"]=None
    # Burn events (liquidity removal) in the forward window
    try:
        lg=[]
        for c in range(r["cblk"], r["cblk"]+801, 400):
            lg+=S.rpc("eth_getLogs",[{"address":r["pair"],"fromBlock":hex(c),
                      "toBlock":hex(min(c+399,r["cblk"]+800)),"topics":[T_BURN]}])
            time.sleep(0.1)
        rec["burns"]=len(lg)
        rec["first_burn_off"]=(min(int(l["blockNumber"],16) for l in lg)-r["mints"][0]["blk"]) if lg else None
    except Exception:
        rec["burns"]=None; rec["first_burn_off"]=None
    sells=[s for s in r["swaps"] if not s["buy"]]
    seatb=[s for s in r["swaps"] if s["buy"]]
    rec["n_sells"]=len(sells); rec["n_buys"]=len(seatb)
    rec["stuck"]=len(sells)==0
    out.append(rec)
    print(f"  [{i+1}/{len(live)}] {r['pair'][:12]} wbnb_now={rec['wbnb_now']} burns={rec['burns']} "
          f"first_burn_off={rec['first_burn_off']} stuck={rec['stuck']}",file=sys.stderr)
json.dump(out,open("data/rugrate.json","w"))

print("\n=== LIQUIDITY STATE NOW (1-3 h after launch) ===")
v=[x["wbnb_now"] for x in out if x["wbnb_now"] is not None]
v.sort()
print(f"  n={len(v)}  median WBNB reserve now = {statistics.median(v):.6f} BNB")
for thr in (0.001,0.01,0.1,1.0):
    print(f"    pairs with < {thr:6.3f} BNB of quote liquidity left: {sum(1 for x in v if x<thr):3d}/{len(v)} ({100*sum(1 for x in v if x<thr)/len(v):.0f}%)")

print("\n=== LIQUIDITY REMOVAL (Burn) inside the 800-blk (~10 min) window ===")
b=[x for x in out if x["burns"] is not None]
print(f"  n={len(b)}  launches with >=1 Burn within 10 min: {sum(1 for x in b if x['burns']>0)}/{len(b)} "
      f"({100*sum(1 for x in b if x['burns']>0)/max(len(b),1):.0f}%)")
fo=[x["first_burn_off"] for x in b if x["first_burn_off"] is not None]
if fo: print(f"  time from liquidity-add to first removal: median={statistics.median(fo)} blk (~{statistics.median(fo)*0.75:.0f} s)  min={min(fo)} max={max(fo)}")

print("\n=== cross-tab: did the pair get rugged vs did anyone manage to sell ===")
for lab,f in [("STUCK (no sell seen)",lambda x:x["stuck"]),("sold",lambda x:not x["stuck"])]:
    g=[x for x in out if f(x) and x["wbnb_now"] is not None]
    if g:
        print(f"  {lab:22s} n={len(g):3d}  median WBNB now={statistics.median([x['wbnb_now'] for x in g]):.6f}  "
              f"burned in 10min={sum(1 for x in g if (x['burns'] or 0)>0)}/{len(g)}")
