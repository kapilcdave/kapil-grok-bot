import json, urllib.request, time, collections, statistics, sys

RPC="https://bsc-rpc.publicnode.com"
TM ="0x5c952063c7fc8610FFDB798152D69F0B9550762b"
T_CREATE="0x396d5e902b675b032348d3d2e9517ee8f0c4a926603fbc075d3d282ff00cad20"
TRADE={"0x0a5575b3648bae2210cee56bf33254cc1ddfbc7bf637c0af2ac18b14fb1bae19",
       "0x7db52723a3b2cdd6164364b3b766e65e540d7be48ffa89582956d8eaebe62942"}

def rpc(m,p,tries=5):
    for i in range(tries):
        try:
            r=urllib.request.Request(RPC,method="POST",
                data=json.dumps({"jsonrpc":"2.0","id":1,"method":m,"params":p}).encode(),
                headers={"Content-Type":"application/json","User-Agent":"curl/8.4.0"})
            d=json.load(urllib.request.urlopen(r,timeout=40))
            if "error" in d: raise RuntimeError(d["error"])
            return d["result"]
        except Exception:
            if i==tries-1: raise
            time.sleep(1.5*(i+1))

def W(data):
    d=data[2:]; return [d[i:i+64] for i in range(0,len(d),64)]

head=int(rpc("eth_blockNumber",[]),16)
END=head-200; START=END-6000          # ~75 min at 0.75s blocks
print(f"head={head} scan [{START},{END}] ~{(END-START)*0.75/60:.0f} min",file=sys.stderr)

creates=[]; trades=[]
for a in range(START,END+1,500):
    b=min(a+499,END)
    for l in rpc("eth_getLogs",[{"address":TM,"fromBlock":hex(a),"toBlock":hex(b)}]):
        t0=l["topics"][0]
        if t0==T_CREATE: creates.append(l)
        elif t0 in TRADE: trades.append(l)
    print(f"  ..{b} creates={len(creates)} trades={len(trades)}",file=sys.stderr)
    time.sleep(0.2)

by_tok=collections.defaultdict(list)
for l in trades:
    by_tok["0x"+W(l["data"])[0][-40:]].append(l)

rows=[]
for l in creates:
    w=W(l["data"])
    tok="0x"+w[1][-40:]; creator="0x"+w[0][-40:]
    cblk,ctxi,ctx=int(l["blockNumber"],16),int(l["transactionIndex"],16),l["transactionHash"]
    tl=sorted(by_tok.get(tok,[]),key=lambda x:(int(x["blockNumber"],16),int(x["transactionIndex"],16),int(x["logIndex"],16)))
    path=[]
    for t in tl:
        tw=W(t["data"]); amt_tok=int(tw[3],16); amt_bnb=int(tw[4],16)
        px = (amt_bnb/amt_tok) if amt_tok else None
        path.append(dict(blk=int(t["blockNumber"],16), txi=int(t["transactionIndex"],16),
                         tx=t["transactionHash"], acct="0x"+tw[1][-40:], px=px,
                         bnb=amt_bnb/1e18, topic=t["topics"][0][:12]))
    rows.append(dict(tok=tok, creator=creator, cblk=cblk, ctxi=ctxi, ctx=ctx, path=path))

json.dump(rows,open("data/bsc_fourmeme_tape.json","w"))
span_hr=(END-START)*0.75/3600
print(f"\n=== four.meme supply ===\n  creates={len(creates)} in {span_hr:.2f} hr -> {len(creates)/span_hr:.0f} launches/hr")
print(f"  trade events={len(trades)} -> {len(trades)/max(len(creates),1):.1f} trades per launch")

WIN=1500
elig=[r for r in rows if r["cblk"]<=END-WIN]
print(f"\n=== eligible (>= {WIN} blk ~19 min forward): {len(elig)} ===")
traded=[r for r in elig if r["path"]]
print(f"  launches with >=1 trade event: {len(traded)}/{len(elig)} ({100*len(traded)/max(len(elig),1):.1f}%)")

atomic=sum(1 for r in traded if r["path"][0]["tx"]==r["ctx"])
print(f"  first trade ATOMIC with create: {atomic}/{len(traded)} ({100*atomic/max(len(traded),1):.1f}%)  <- unreachable seat")

print("\n=== competition proxy: DISTINCT txs on the token within N blocks of create ===")
for N in (0,1,3,10,50):
    c=[len({p['tx'] for p in r['path'] if p['blk']-r['cblk']<=N}) for r in traded]
    if c: print(f"  within {N:2d} blk: mean={statistics.mean(c):5.2f} median={statistics.median(c):4.1f} max={max(c):3d}  >=2 txs: {sum(1 for x in c if x>=2)}/{len(c)}")

# distinct non-creator accounts in creation block = real racers
rac=[len({p['acct'] for p in r['path'] if p['blk']==r['cblk'] and p['acct'].lower()!=r['creator'].lower()}) for r in traded]
if rac: print(f"  distinct NON-CREATOR accounts in creation block: mean={statistics.mean(rac):.2f} max={max(rac)}  >=1: {sum(1 for x in rac if x>=1)}/{len(rac)}")

print("\n=== seat P&L: first reachable (non-create-tx) seat -> last observed price ===")
res=[]
for r in traded:
    ent=next((p for p in r["path"] if p["tx"]!=r["ctx"] and p["px"]),None)
    if not ent: continue
    later=[p for p in r["path"] if p["px"] and (p["blk"],p["txi"])>(ent["blk"],ent["txi"])]
    if not later: continue
    res.append(later[-1]["px"]/ent["px"]-1.0)
if len(res)>=5:
    res.sort()
    print(f"  n={len(res)} mean={100*statistics.mean(res):+.2f}% median={100*statistics.median(res):+.2f}% "
          f"p10={100*res[len(res)//10]:+.1f}% p90={100*res[9*len(res)//10]:+.1f}%")
else:
    print(f"  n={len(res)} — too few to score")
