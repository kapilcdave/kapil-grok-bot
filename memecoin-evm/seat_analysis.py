import json, collections, statistics
rows=json.load(open("data/base_v4_tape.json"))
END=max(r["cblk"] for r in rows)
WINDOW=400   # require >=400 blocks (~13 min) of forward data to judge "never traded"
elig=[r for r in rows if r["cblk"] <= END-WINDOW]

def pad(r):
    h,q=r["hook"][:10],r["quote"]
    if h=="0x0469a4bd": return "zora-creator"
    if q=="ZORA": return "zora-other"
    return f"{h}/{q}"

print(f"total launches={len(rows)}  eligible (>= {WINDOW} blk forward)={len(elig)}\n")
print(f"{'launchpad':16s} {'n':>4s} {'traded':>7s} {'%traded':>8s} {'atomic':>7s} {'med 1st-swap blk':>17s} {'med swaps':>10s}")
g=collections.defaultdict(list)
for r in elig: g[pad(r)].append(r)
for k,v in sorted(g.items(), key=lambda x:-len(x[1])):
    tr=[r for r in v if r["nswaps"]>0]
    at=sum(1 for r in tr if r["path"][1][2]==r["ctx"])
    offs=[r["path"][1][0]-r["cblk"] for r in tr]
    print(f"{k:16s} {len(v):4d} {len(tr):7d} {100*len(tr)/len(v):7.1f}% {at:7d} "
          f"{statistics.median(offs) if offs else float('nan'):17.1f} {statistics.median([r['nswaps'] for r in tr]) if tr else 0:10.1f}")

# --- for launches that actually traded: how many DISTINCT txs in the first 3 blocks? (competition proxy)
print("\n=== competition proxy: txs touching the pool within N blocks of create ===")
for N in (0,1,3,10,30):
    cnt=[]
    for r in elig:
        if r["nswaps"]==0: continue
        tx={p[2] for p in r["path"][1:] if p[0]-r["cblk"]<=N}
        cnt.append(len(tx))
    if cnt:
        print(f"  within {N:2d} blk: mean={statistics.mean(cnt):5.2f} median={statistics.median(cnt):4.1f} "
              f"max={max(cnt):3d}  pools with >=2 txs: {sum(1 for c in cnt if c>=2)}/{len(cnt)}")

# --- seat return: buy at first NON-atomic opportunity, mark at horizon
print("\n=== seat P&L (traded, non-dead pools) ===")
for name,v in sorted(g.items(), key=lambda x:-len(x[1])):
    res=[]
    for r in v:
        if r["nswaps"]<2: continue
        p=r["path"]
        # first reachable seat = first swap NOT in the create tx
        entry=None
        for q in p[1:]:
            if q[2]!=r["ctx"] and q[3]: entry=q; break
        if not entry: continue
        last=[q for q in p[1:] if q[3]]
        if not last: continue
        exit_=last[-1]
        if exit_[0]<=entry[0]: continue
        res.append(exit_[3]/entry[3]-1.0)
    if len(res)>=5:
        res.sort()
        print(f"  {name:16s} n={len(res):3d} mean={100*statistics.mean(res):+8.2f}%  "
              f"median={100*statistics.median(res):+7.2f}%  p10={100*res[len(res)//10]:+7.1f}% p90={100*res[9*len(res)//10]:+8.1f}%")
