import json, urllib.request, time, collections, statistics, sys, itertools
RPCS=["https://bsc-rpc.publicnode.com","https://bsc.drpc.org","https://binance.llamarpc.com","https://bsc-dataseed1.defibit.io"]
_rr=itertools.cycle(RPCS)
FACTORY="0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73"
T_PC="0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9"
T_SWAP="0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
T_MINT="0x4c209b5fc8ad50758f13e2e1088ba56a560dff690a1c6fef26394f4c03821c4f"
T_SYNC="0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1"
QUOTES={"0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c":"WBNB",
        "0x55d398326f99059ff775485246999027b3197955":"USDT",
        "0xe9e7cea3dedca5984780bafc599bd69add087d56":"BUSD",
        "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d":"USDC"}
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
FWD=800
END=head-FWD-20; START=END-3600
pairs=[]
for a in range(START,END+1,400):
    for l in rpc("eth_getLogs",[{"address":FACTORY,"fromBlock":hex(a),"toBlock":hex(min(a+399,END)),"topics":[T_PC]}]):
        t0="0x"+l["topics"][1][-40:]; t1="0x"+l["topics"][2][-40:]
        q0,q1=t0.lower() in QUOTES, t1.lower() in QUOTES
        if q0==q1: continue
        pairs.append(dict(pair="0x"+W(l["data"])[0][-40:],cblk=int(l["blockNumber"],16),
                          ctx=l["transactionHash"],qt0=q0,
                          qsym=QUOTES[(t0 if q0 else t1).lower()]))
    time.sleep(0.3)
print(f"candidate pairs: {len(pairs)}",file=sys.stderr)

rows=[]
for i,p in enumerate(pairs):
    got=[]
    try:
        for c in range(p["cblk"], p["cblk"]+FWD+1, 400):
            got+=rpc("eth_getLogs",[{"address":p["pair"],"fromBlock":hex(c),"toBlock":hex(min(c+399,p["cblk"]+FWD))}])
            time.sleep(0.2)
    except Exception as e:
        print(f"  [{i}] fail {str(e)[:40]}",file=sys.stderr); continue
    sw=[];mn=[]
    for l in got:
        t=l["topics"][0]; w=W(l["data"])
        base=dict(blk=int(l["blockNumber"],16),txi=int(l["transactionIndex"],16),tx=l["transactionHash"])
        if t==T_SWAP:
            a0i,a1i,a0o,a1o=[int(x,16) for x in w[:4]]
            if p["qt0"]: qin,qout,tin,tout=a0i,a0o,a1i,a1o
            else:        qin,qout,tin,tout=a1i,a1o,a0i,a0o
            buy=qin>0
            tamt=tout if buy else tin
            qamt=qin if buy else qout
            base.update(buy=buy,q=qamt/1e18,t=tamt,
                        to="0x"+l["topics"][2][-40:] if len(l["topics"])>2 else None,
                        px=(qamt/tamt) if tamt else None)
            sw.append(base)
        elif t==T_MINT: mn.append(base)
    if sw or mn:
        rows.append(dict(**{k:p[k] for k in("pair","cblk","ctx","qsym")},swaps=sw,mints=mn))
    if i%15==0: print(f"  [{i}/{len(pairs)}] rows={len(rows)}",file=sys.stderr)
json.dump(rows,open("data/bsc_pancake_open_tape.json","w"))

live=[r for r in rows if r["mints"] and r["swaps"]]
print(f"\n=== n={len(live)} pairs with liquidity AND trades ({FWD} blk ~10 min forward) ===")

print("\n[1] LAG from liquidity-add to FIRST swap  (anti-bot / enableTrading gate)")
lag=[r["swaps"][0]["blk"]-r["mints"][0]["blk"] for r in live]
lag.sort()
print(f"    median={statistics.median(lag)} blk  mean={statistics.mean(lag):.1f}  p10={lag[len(lag)//10]}  p90={lag[9*len(lag)//10]}  max={max(lag)}")
print(f"    first swap in the SAME block as liquidity: {sum(1 for x in lag if x==0)}/{len(lag)}")

print("\n[2] COMPETITION at the real open (distinct txs in the FIRST-SWAP block)")
c0=[len({s['tx'] for s in r['swaps'] if s['blk']==r['swaps'][0]['blk']}) for r in live]
print(f"    mean={statistics.mean(c0):.2f} median={statistics.median(c0):.1f} max={max(c0)}  >=2: {sum(1 for x in c0 if x>=2)}/{len(c0)}  >=5: {sum(1 for x in c0 if x>=5)}")
for N in (1,3,10):
    c=[len({s['tx'] for s in r['swaps'] if 0<=s['blk']-r['swaps'][0]['blk']<=N}) for r in live]
    print(f"    within +{N:2d} blk of open: mean={statistics.mean(c):5.2f} median={statistics.median(c):4.1f} >=5: {sum(1 for x in c if x>=5)}/{len(c)}")

print("\n[3] SELLABILITY — the honeypot screen")
nb=ns=0; bq=sq=0.0; nosell=0
for r in live:
    b=[s for s in r["swaps"] if s["buy"]]; s_=[s for s in r["swaps"] if not s["buy"]]
    nb+=len(b); ns+=len(s_); bq+=sum(x["q"] for x in b); sq+=sum(x["q"] for x in s_)
    if not s_: nosell+=1
print(f"    buy swaps={nb}  sell swaps={ns}   sell/buy count ratio={ns/max(nb,1):.3f}")
print(f"    buy quote vol={bq:.2f}  sell quote vol={sq:.2f}   sell/buy VALUE ratio={sq/max(bq,1e-9):.3f}")
print(f"    launches with ZERO sells in {FWD} blk: {nosell}/{len(live)} ({100*nosell/max(len(live),1):.1f}%)  <- honeypot proxy")
per=[(len([s for s in r['swaps'] if not s['buy']]), len([s for s in r['swaps'] if s['buy']])) for r in live]
print(f"    median sells per launch={statistics.median([x[0] for x in per])}  median buys per launch={statistics.median([x[1] for x in per])}")

print("\n[4] SEAT P&L — enter at first reachable tx in the open block, exit at best later sell-side price")
res=[]
for r in live:
    sw=r["swaps"]
    ent=next((s for s in sw if s["px"] and s["tx"]!=r["mints"][0]["tx"]),None)
    if not ent: continue
    later=[s for s in sw if s["px"] and (s["blk"],s["txi"])>(ent["blk"],ent["txi"])]
    if not later: continue
    res.append(dict(pair=r["pair"], ret=later[-1]["px"]/ent["px"]-1.0,
                    mx=max(s["px"] for s in later)/ent["px"]-1.0,
                    sells_after=sum(1 for s in later if not s["buy"])))
if len(res)>=5:
    rr=sorted(x["ret"] for x in res)
    print(f"    hold-to-last n={len(rr)} mean={100*statistics.mean(rr):+8.2f}% median={100*statistics.median(rr):+7.2f}% p10={100*rr[len(rr)//10]:+7.1f}% p90={100*rr[9*len(rr)//10]:+8.1f}%")
    ex=[x for x in res if x["sells_after"]>0]
    print(f"    restricted to launches where a SELL actually occurred after our seat: n={len(ex)}")
    if len(ex)>=5:
        er=sorted(x["ret"] for x in ex)
        print(f"      mean={100*statistics.mean(er):+8.2f}% median={100*statistics.median(er):+7.2f}% p10={100*er[len(er)//10]:+7.1f}% p90={100*er[9*len(er)//10]:+8.1f}%")
