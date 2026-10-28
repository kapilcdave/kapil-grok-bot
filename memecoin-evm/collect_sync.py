"""Collect PancakeSwap-v2 launch tapes INCLUDING Sync (reserves) events.

Scans the public log-retention window for PairCreated, then pulls every event on
each pair for 900 blocks forward. Resumable: appends to JSONL, skips pairs done.
"""
import json, urllib.request, time, sys, itertools, os
RPCS=["https://bsc-rpc.publicnode.com"]   # only endpoint that serves getLogs reliably
_rr=itertools.cycle(RPCS)
# A 403 here is transient throttling, not a ban: the endpoint answers again within
# seconds. The old policy slept 20*(i+1) over 10 tries = 900 s per failed call, and the
# factory scan is 3 chunks x (1 + 4 sub-chunks) = 13,500 s = 3.75 h. Observed: one run
# hung exactly that long and collected nothing. Worse, sleeping is self-defeating --
# public BSC log retention is ~1.2-2.4 h, so waiting 15 min to retry a block range is
# what makes it "unrecoverable". The scarce resource is the retention window, not the
# request. Fail fast, lose the chunk, let the next cycle (70 min) cover it via overlap.
DEADLINE_S=int(os.environ.get("DEADLINE_S","1200"))
_t0=time.time()
def over_deadline(): return time.time()-_t0 > DEADLINE_S
FACTORY="0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73"
T_PC  ="0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9"
T_SWAP="0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
T_MINT="0x4c209b5fc8ad50758f13e2e1088ba56a560dff690a1c6fef26394f4c03821c4f"
T_BURN="0xdccd412f0b1252819cb1fd330b93224ca42612892bb3f4f789976e6d81936496"
T_SYNC="0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1"
WBNB  ="0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
FWD=900; OUT="data/sync_tape.jsonl"

def rpc(m,p,tries=4):
    last=None
    for i in range(tries):
        try:
            u=next(_rr)
            r=urllib.request.Request(u,method="POST",
                data=json.dumps({"jsonrpc":"2.0","id":1,"method":m,"params":p}).encode(),
                headers={"Content-Type":"application/json","User-Agent":"curl/8.4.0"})
            d=json.load(urllib.request.urlopen(r,timeout=20))
            if "error" in d: raise RuntimeError(str(d["error"])[:60])
            return d["result"]
        except Exception as e:
            last=e
            time.sleep(min(2**i,8))       # <=15 s total, then give the chunk up
    raise last
def W(d): d=d[2:]; return [d[i:i+64] for i in range(0,len(d),64)]

done=set()
if os.path.exists(OUT):
    for ln in open(OUT):
        try: done.add(json.loads(ln)["pair"])
        except Exception: pass
print(f"resume: {len(done)} pairs already collected",file=sys.stderr)

head=int(rpc("eth_blockNumber",[]),16)
END=head-FWD-10; START=head-11200      # ~2.3 h back, inside retention
print(f"head={head} PairCreated [{START},{END}]",file=sys.stderr)

pairs=[]
for a in range(START,END+1,4000):
    b=min(a+3999,END)
    if over_deadline():
        print(f"  DEADLINE: abandoning factory scan at block {a}",file=sys.stderr); break
    try:
        lg=rpc("eth_getLogs",[{"address":FACTORY,"fromBlock":hex(a),"toBlock":hex(b),"topics":[T_PC]}])
    except Exception as e:
        lg=[]
        for sa in range(a,b+1,1000):            # degrade to 1000-blk sub-chunks
            try:
                lg+=rpc("eth_getLogs",[{"address":FACTORY,"fromBlock":hex(sa),
                        "toBlock":hex(min(sa+999,b)),"topics":[T_PC]}])
                time.sleep(1.0)
            except Exception as e2:
                print(f"  GAP: factory blocks {sa}-{min(sa+999,b)} unrecoverable ({e2})",file=sys.stderr)
    for l in lg:
        t0="0x"+l["topics"][1][-40:]; t1="0x"+l["topics"][2][-40:]
        isw0, isw1 = t0.lower()==WBNB, t1.lower()==WBNB
        if isw0==isw1: continue                 # need exactly one WBNB leg
        pr="0x"+W(l["data"])[0][-40:]
        if pr in done: continue
        pairs.append(dict(pair=pr,cblk=int(l["blockNumber"],16),ctx=l["transactionHash"],
                          wbnb_is_t0=isw0, token=(t1 if isw0 else t0)))
    time.sleep(1.2)
print(f"new WBNB candidate pairs: {len(pairs)}",file=sys.stderr)

fh=open(OUT,"a"); kept=0; failed=0; skipped_deadline=0
for i,p in enumerate(pairs):
    if over_deadline():
        skipped_deadline=len(pairs)-i
        print(f"  DEADLINE: {skipped_deadline} pairs left unfetched this cycle",file=sys.stderr)
        break
    logs=[]; ok=True
    try:
        logs=rpc("eth_getLogs",[{"address":p["pair"],"fromBlock":hex(p["cblk"]),
                  "toBlock":hex(p["cblk"]+FWD)}])
    except Exception as e:
        ok=False
    time.sleep(0.9)
    if not ok:
        failed+=1
        print(f"  [{i+1}/{len(pairs)}] {p['pair'][:12]} CHUNK FAIL (skipped)",file=sys.stderr)
        continue
    sw=[];mn=[];bn=[];sy=[]
    for l in logs:
        t=l["topics"][0]; w=W(l["data"])
        k=dict(blk=int(l["blockNumber"],16),txi=int(l["transactionIndex"],16),
               li=int(l["logIndex"],16),tx=l["transactionHash"])
        if t==T_SWAP:
            a0i,a1i,a0o,a1o=[int(x,16) for x in w[:4]]
            qin,qout,tin,tout=(a0i,a0o,a1i,a1o) if p["wbnb_is_t0"] else (a1i,a1o,a0i,a0o)
            k.update(buy=qin>0, q=(qin if qin>0 else qout), t=(tout if qin>0 else tin)); sw.append(k)
        elif t==T_SYNC:
            r0,r1=int(w[0],16),int(w[1],16)
            rq,rt=(r0,r1) if p["wbnb_is_t0"] else (r1,r0)
            k.update(rq=rq, rt=rt); sy.append(k)
        elif t==T_MINT: mn.append(k)
        elif t==T_BURN: bn.append(k)
    if mn and sw and sy:
        fh.write(json.dumps(dict(pair=p["pair"],token=p["token"],cblk=p["cblk"],ctx=p["ctx"],
                 swaps=sw,mints=mn,burns=bn,syncs=sy))+"\n"); fh.flush(); kept+=1
    if (i+1)%20==0: print(f"  [{i+1}/{len(pairs)}] kept={kept} failed={failed}",file=sys.stderr)
print(f"COLLECT DONE kept={kept} failed={failed} deadline_skipped={skipped_deadline} "
      f"elapsed={time.time()-_t0:.0f}s total_rows={len(done)+kept}",file=sys.stderr)
