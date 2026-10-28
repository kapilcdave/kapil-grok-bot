import json, urllib.request, time, collections, statistics, sys, itertools
RPCS=["https://bsc-rpc.publicnode.com","https://bsc.drpc.org","https://binance.llamarpc.com","https://bsc-dataseed1.defibit.io"]
_rr=itertools.cycle(RPCS)
FACTORY="0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73"          # PancakeSwap V2 factory
T_PAIRCREATED="0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9"
T_SWAP="0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
T_MINT="0x4c209b5fc8ad50758f13e2e1088ba56a560dff690a1c6fef26394f4c03821c4f"
QUOTES={"0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c":("WBNB",1e18),
        "0x55d398326f99059ff775485246999027b3197955":("USDT",1e18),
        "0xe9e7cea3dedca5984780bafc599bd69add087d56":("BUSD",1e18),
        "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d":("USDC",1e18)}
def rpc(m,p,tries=6):
    last=None
    for i in range(tries):
        try:
            u=next(_rr)
            r=urllib.request.Request(u,method="POST",data=json.dumps({"jsonrpc":"2.0","id":1,"method":m,"params":p}).encode(),
                headers={"Content-Type":"application/json","User-Agent":"curl/8.4.0"})
            d=json.load(urllib.request.urlopen(r,timeout=35))
            if "error" in d: raise RuntimeError(d["error"])
            return d["result"]
        except Exception as e:
            last=e; time.sleep(0.8*(i+1))
    raise last
def W(d): d=d[2:]; return [d[i:i+64] for i in range(0,len(d),64)]

head=int(rpc("eth_blockNumber",[]),16)
FWD=400                                    # ~5 min of forward data per pair
END=head-FWD-20; START=END-3200            # ~40 min of pair creations
print(f"head={head} PairCreated scan [{START},{END}] ~{(END-START)*0.75/60:.0f} min",file=sys.stderr)

pairs=[]
for a in range(START,END+1,400):
    b=min(a+399,END)
    for l in rpc("eth_getLogs",[{"address":FACTORY,"fromBlock":hex(a),"toBlock":hex(b),"topics":[T_PAIRCREATED]}]):
        t0="0x"+l["topics"][1][-40:]; t1="0x"+l["topics"][2][-40:]
        pair="0x"+W(l["data"])[0][-40:]
        pairs.append(dict(pair=pair,t0=t0,t1=t1,cblk=int(l["blockNumber"],16),
                          ctxi=int(l["transactionIndex"],16),ctx=l["transactionHash"]))
    print(f"  ..{b} pairs={len(pairs)}",file=sys.stderr); time.sleep(0.3)

hrs=(END-START)*0.75/3600
print(f"\nPancakeSwap V2 PairCreated: {len(pairs)} in {hrs:.2f} hr -> {len(pairs)/hrs:.0f} pairs/hr",file=sys.stderr)

# keep only pairs with a recognised quote leg
cand=[]
for p in pairs:
    q0,q1=p["t0"].lower() in QUOTES, p["t1"].lower() in QUOTES
    if q0==q1: continue
    p["quote_is_t0"]=q0
    p["qsym"],p["qdec"]=QUOTES[(p["t0"] if q0 else p["t1"]).lower()]
    cand.append(p)
print(f"with a recognised quote leg: {len(cand)}",file=sys.stderr)

rows=[]
for i,p in enumerate(cand):
    try:
        lg=rpc("eth_getLogs",[{"address":p["pair"],"fromBlock":hex(p["cblk"]),"toBlock":hex(p["cblk"]+FWD)}])
    except Exception as e:
        print(f"  [{i}] {p['pair']} FAILED {str(e)[:50]}",file=sys.stderr); continue
    sw=[];mn=[]
    for l in lg:
        t=l["topics"][0]; w=W(l["data"])
        rec=dict(blk=int(l["blockNumber"],16),txi=int(l["transactionIndex"],16),tx=l["transactionHash"])
        if t==T_SWAP:
            a0i,a1i,a0o,a1o=[int(x,16) for x in w[:4]]
            qin,qout=(a0i,a0o) if p["quote_is_t0"] else (a1i,a1o)
            rec.update(qvol=(qin+qout)/p["qdec"], is_buy=qin>0); sw.append(rec)
        elif t==T_MINT: mn.append(rec)
    rows.append(dict(**{k:p[k] for k in("pair","cblk","ctx","qsym")}, swaps=sw, mints=mn))
    if i%15==0: print(f"  [{i}/{len(cand)}] {len(rows)} done",file=sys.stderr)
    time.sleep(0.25)

json.dump(rows,open("data/bsc_pancake_v2_tape.json","w"))
print(f"\n=== PancakeSwap V2 direct launches (n={len(rows)}, {FWD} blk ~5 min forward) ===")
print(f"  pairs/hr (all)            : {len(pairs)/hrs:.0f}")
print(f"  with quote leg            : {len(cand)}")
nolq=sum(1 for r in rows if not r["mints"])
print(f"  NEVER got liquidity       : {nolq}/{len(rows)}  ({100*nolq/max(len(rows),1):.1f}%)")
live=[r for r in rows if r["mints"]]
notr=sum(1 for r in live if not r["swaps"])
print(f"  liquidity but NO trade    : {notr}/{len(live)}")
trd=[r for r in live if r["swaps"]]
print(f"  traded                    : {len(trd)}/{len(live)}")
if trd:
    atom=sum(1 for r in trd if r["swaps"][0]["tx"]==r["mints"][0]["tx"])
    print(f"  1st swap ATOMIC with liquidity-add tx: {atom}/{len(trd)} ({100*atom/len(trd):.1f}%)")
    print("\n  === competition: distinct txs swapping within N blocks of the liquidity add ===")
    for N in (0,1,3,10,50):
        c=[len({s['tx'] for s in r['swaps'] if 0<=s['blk']-r['mints'][0]['blk']<=N}) for r in trd]
        print(f"    within {N:2d} blk: mean={statistics.mean(c):5.2f} median={statistics.median(c):4.1f} max={max(c):3d}  >=2 txs: {sum(1 for x in c if x>=2)}/{len(c)}  >=5: {sum(1 for x in c if x>=5)}")
    print("\n  === follower volume per launch (quote units, 5 min) ===")
    for sym in sorted({r["qsym"] for r in trd}):
        v=sorted(sum(s["qvol"] for s in r["swaps"]) for r in trd if r["qsym"]==sym)
        if len(v)>=3:
            print(f"    {sym:5s} n={len(v):3d} median={statistics.median(v):10.4f} mean={statistics.mean(v):11.4f} p90={v[9*len(v)//10]:11.4f} max={max(v):12.2f}")
    allv=sorted(sum(s["qvol"] for s in r["swaps"]) for r in trd)
    print(f"    launches with >0 follower swaps beyond the atomic one: "
          f"{sum(1 for r in trd if len({s['tx'] for s in r['swaps']})>1)}/{len(trd)}")
