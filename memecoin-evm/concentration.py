import json, urllib.request, time, collections, statistics, sys
RPC="https://bsc-rpc.publicnode.com"; TM="0x5c952063c7fc8610FFDB798152D69F0B9550762b"
T_CREATE="0x396d5e902b675b032348d3d2e9517ee8f0c4a926603fbc075d3d282ff00cad20"
TRADE={"0x0a5575b3648bae2210cee56bf33254cc1ddfbc7bf637c0af2ac18b14fb1bae19",
       "0x7db52723a3b2cdd6164364b3b766e65e540d7be48ffa89582956d8eaebe62942"}
def rpc(m,p,tries=5):
    for i in range(tries):
        try:
            r=urllib.request.Request(RPC,method="POST",data=json.dumps({"jsonrpc":"2.0","id":1,"method":m,"params":p}).encode(),
                headers={"Content-Type":"application/json","User-Agent":"curl/8.4.0"})
            d=json.load(urllib.request.urlopen(r,timeout=40))
            if "error" in d: raise RuntimeError(d["error"])
            return d["result"]
        except Exception:
            if i==tries-1: raise
            time.sleep(1.5*(i+1))
def W(d): d=d[2:]; return [d[i:i+64] for i in range(0,len(d),64)]

head=int(rpc("eth_blockNumber",[]),16); END=head-200; START=END-6000
tr=collections.Counter(); bnb=collections.Counter(); created=set()
for a in range(START,END+1,500):
    for l in rpc("eth_getLogs",[{"address":TM,"fromBlock":hex(a),"toBlock":hex(min(a+499,END))}]):
        t0=l["topics"][0]; w=W(l["data"])
        if t0==T_CREATE: created.add("0x"+w[1][-40:])
        elif t0 in TRADE:
            tok="0x"+w[0][-40:]; tr[tok]+=1; bnb[tok]+=int(w[4],16)/1e18
    time.sleep(0.2)

tot=sum(tr.values()); print(f"tokens traded in window: {len(tr)}   trade events: {tot}")
top=tr.most_common()
for k in (1,3,5,10,20):
    s=sum(n for _,n in top[:k]); print(f"  top {k:2d} tokens = {100*s/tot:5.1f}% of all trades")
print(f"\n  trades landing on a token CREATED in this window: {sum(n for t,n in tr.items() if t in created)}/{tot} "
      f"({100*sum(n for t,n in tr.items() if t in created)/tot:.1f}%)")
print(f"  tokens created in window that EVER traded: {len([t for t in created if tr[t]])}/{len(created)}")

rows=json.load(open("data/bsc_fourmeme_tape.json"))
pl=[len(r["path"]) for r in rows]
print(f"\n=== per-launch trade events (in-window creates, n={len(pl)}) ===")
print(f"  mean={statistics.mean(pl):.2f} median={statistics.median(pl):.1f} max={max(pl)}")
h=collections.Counter(min(p,6) for p in pl)
for k in sorted(h): print(f"   {k if k<6 else '6+'} trades: {h[k]:3d} launches")

print(f"\n=== BNB volume per launch (in-window creates) ===")
v=sorted(bnb[r["tok"]] for r in rows)
print(f"  median={statistics.median(v):.4f} BNB   mean={statistics.mean(v):.4f}   p90={v[9*len(v)//10]:.4f}   max={max(v):.3f}")
print(f"  launches with >0.1 BNB of lifetime volume: {sum(1 for x in v if x>0.1)}/{len(v)}")
print(f"  launches with >1.0 BNB of lifetime volume: {sum(1 for x in v if x>1.0)}/{len(v)}")
