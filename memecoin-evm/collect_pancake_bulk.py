import json, urllib.request, time, sys, itertools, os
RPCS=["https://bsc-rpc.publicnode.com","https://bsc.drpc.org","https://binance.llamarpc.com",
      "https://bsc-dataseed1.defibit.io","https://bsc-dataseed2.bnbchain.org"]
_rr=itertools.cycle(RPCS)
FACTORY="0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73"
T_PC="0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9"
T_SWAP="0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
T_MINT="0x4c209b5fc8ad50758f13e2e1088ba56a560dff690a1c6fef26394f4c03821c4f"
QUOTES={"0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c":"WBNB","0x55d398326f99059ff775485246999027b3197955":"USDT",
        "0xe9e7cea3dedca5984780bafc599bd69add087d56":"BUSD","0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d":"USDC"}
def rpc(m,p,tries=8):
    last=None
    for i in range(tries):
        try:
            u=next(_rr)
            r=urllib.request.Request(u,method="POST",data=json.dumps({"jsonrpc":"2.0","id":1,"method":m,"params":p}).encode(),
                headers={"Content-Type":"application/json","User-Agent":"curl/8.4.0"})
            d=json.load(urllib.request.urlopen(r,timeout=30))
            if "error" in d: raise RuntimeError(d["error"])
            return d["result"]
        except Exception as e:
            last=e; time.sleep(0.5*(i+1))
    raise last
def W(d): d=d[2:]; return [d[i:i+64] for i in range(0,len(d),64)]

OUT="data/pancake_bulk.jsonl"
done=set()
if os.path.exists(OUT):
    for ln in open(OUT):
        try: done.add(json.loads(ln)["pair"])
        except Exception: pass
for f in ("data/bsc_pancake_open_tape.json",):
    if os.path.exists(f):
        for r in json.load(open(f)): done.add(r["pair"])
print(f"already have {len(done)} pairs",file=sys.stderr)

head=int(rpc("eth_blockNumber",[]),16)
FWD=800
END=head-FWD-20
START=head-11500          # ~2.4 h, the public log retention limit
pairs=[]
for a in range(START,END+1,400):
    try:
        lg=rpc("eth_getLogs",[{"address":FACTORY,"fromBlock":hex(a),"toBlock":hex(min(a+399,END)),"topics":[T_PC]}])
    except Exception as e:
        print(f"  factory {a} FAIL {str(e)[:40]}",file=sys.stderr); continue
    for l in lg:
        t0="0x"+l["topics"][1][-40:]; t1="0x"+l["topics"][2][-40:]
        q0,q1=t0.lower() in QUOTES, t1.lower() in QUOTES
        if q0==q1: continue
        pr="0x"+W(l["data"])[0][-40:]
        if pr in done: continue
        pairs.append(dict(pair=pr,cblk=int(l["blockNumber"],16),ctx=l["transactionHash"],
                          qt0=q0,qsym=QUOTES[(t0 if q0 else t1).lower()]))
    time.sleep(0.15)
print(f"new candidate pairs: {len(pairs)}",file=sys.stderr)

fh=open(OUT,"a")
for i,p in enumerate(pairs):
    got=[]; ok=True
    for c in range(p["cblk"], p["cblk"]+FWD+1, 400):
        try:
            got+=rpc("eth_getLogs",[{"address":p["pair"],"fromBlock":hex(c),"toBlock":hex(min(c+399,p["cblk"]+FWD))}])
        except Exception: ok=False; break
        time.sleep(0.12)
    if not ok: continue
    sw=[];mn=[]
    for l in got:
        t=l["topics"][0]; w=W(l["data"])
        base=dict(blk=int(l["blockNumber"],16),txi=int(l["transactionIndex"],16),tx=l["transactionHash"])
        if t==T_SWAP:
            a0i,a1i,a0o,a1o=[int(x,16) for x in w[:4]]
            qin,qout,tin,tout=(a0i,a0o,a1i,a1o) if p["qt0"] else (a1i,a1o,a0i,a0o)
            buy=qin>0; tamt=tout if buy else tin; qamt=qin if buy else qout
            base.update(buy=buy,q=qamt/1e18,px=(qamt/tamt) if tamt else None); sw.append(base)
        elif t==T_MINT: mn.append(base)
    if sw or mn:
        fh.write(json.dumps(dict(**{k:p[k] for k in("pair","cblk","ctx","qsym")},swaps=sw,mints=mn))+"\n"); fh.flush()
    if i%20==0: print(f"  [{i}/{len(pairs)}]",file=sys.stderr)
print("COLLECT DONE",file=sys.stderr)
