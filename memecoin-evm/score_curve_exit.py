"""Score the preregistered curve-priced exit test.
Config and decision rule are frozen in preregistrations/bsc_pancake_curve_exit_20261012.md
"""
import json, statistics, random, sys, os

FEE_N, FEE_D = 997, 1000          # PancakeSwap v2 0.25%
CLIPS = [0.01, 0.05]              # BNB, frozen
HORIZONS = [1, 2, 5, 10, 30]      # blocks, frozen
BOOT = 20000
random.seed(20261012)

def amount_out(ain, rin, rout):
    if ain<=0 or rin<=0 or rout<=0: return 0
    return (FEE_N*ain*rout)//(FEE_D*rin + FEE_N*ain)

rows=[json.loads(l) for l in open("data/sync_tape.jsonl")]
print(f"launches collected: {len(rows)}")

def last_sync_at_or_before(syncs, blk, li=None):
    best=None
    for s in syncs:
        if s["blk"]<blk or (li is not None and s["blk"]==blk and s["li"]<=li):
            if best is None or (s["blk"],s["li"])>(best["blk"],best["li"]): best=s
    return best

results={}
skipped=0
per_launch=[]
for r in rows:
    sw=sorted(r["swaps"], key=lambda x:(x["blk"],x["txi"],x["li"]))
    sy=sorted(r["syncs"], key=lambda x:(x["blk"],x["txi"],x["li"]))
    if not sw or not sy: skipped+=1; continue
    open_blk=sw[0]["blk"]
    pre=last_sync_at_or_before(sy, open_blk)      # strictly before the open block
    if pre is None or pre["rq"]<=0 or pre["rt"]<=0: skipped+=1; continue
    rec={"pair":r["pair"],"cblk":r["cblk"],"open_blk":open_blk}
    for C in CLIPS:
        cw=int(C*1e18)
        T=amount_out(cw, pre["rq"], pre["rt"])
        if T<=0: continue
        for N in HORIZONS:
            ex=last_sync_at_or_before(sy, open_blk+N+1)
            if ex is None: continue
            B=amount_out(T, ex["rt"], ex["rq"])
            rec[f"ret_{C}_{N}"]=(B-cw)/cw
    per_launch.append(rec)

print(f"scored launches: {len(per_launch)}   skipped (no priceable entry): {skipped}\n")

def boot_ci(v):
    m=[statistics.mean(random.choices(v,k=len(v))) for _ in range(BOOT)]
    m.sort(); return m[int(0.025*BOOT)], m[int(0.975*BOOT)]

print("=== PRIMARY: mean realised return per launch, bootstrap 95% CI ===")
print(f"{'clip':>6} {'N':>3} {'n':>4} {'mean':>10} {'95% CI':>24} {'median':>9} {'win':>5}")
passes=[]
for C in CLIPS:
    for N in HORIZONS:
        v=[x[f"ret_{C}_{N}"] for x in per_launch if f"ret_{C}_{N}" in x]
        if len(v)<5: continue
        lo,hi=boot_ci(v)
        star=" <== CI>0" if lo>0 else ""
        print(f"{C:>6} {N:>3} {len(v):>4} {100*statistics.mean(v):>9.2f}% "
              f"[{100*lo:>+8.2f}%,{100*hi:>+8.2f}%] {100*statistics.median(v):>8.2f}% "
              f"{100*sum(1 for x in v if x>0)/len(v):>4.0f}%{star}")
        if lo>0: passes.append((C,N,v))

print("\n=== REPLICATION ARM (chronological split, required for a PASS) ===")
if not passes:
    print("  no (clip,N) cell has a CI strictly above 0 -> replication arm not reached")
else:
    for C,N,_ in passes:
        pl=sorted([x for x in per_launch if f"ret_{C}_{N}" in x], key=lambda x:x["cblk"])
        h=len(pl)//2
        a=[x[f"ret_{C}_{N}"] for x in pl[:h]]; b=[x[f"ret_{C}_{N}"] for x in pl[h:]]
        alo,ahi=boot_ci(a)
        print(f"  clip={C} N={N}: first half n={len(a)} mean={100*statistics.mean(a):+.2f}% "
              f"CI[{100*alo:+.2f}%,{100*ahi:+.2f}%] | second half n={len(b)} mean={100*statistics.mean(b):+.2f}%")
        ok = alo>0 and statistics.mean(b)>0
        print(f"    -> {'PASS' if ok else 'FAIL'} (needs first-half CI>0 AND second-half mean>0)")

print("\n=== descriptive: how often is the exit pool already drained? ===")
for C in [0.01]:
    for N in HORIZONS:
        v=[x[f"ret_{C}_{N}"] for x in per_launch if f"ret_{C}_{N}" in x]
        if v: print(f"  N={N:>2}: returns below -90%: {sum(1 for x in v if x<-0.9):>3}/{len(v)}  "
                    f"below -50%: {sum(1 for x in v if x<-0.5):>3}  above 0: {sum(1 for x in v if x>0):>3}")
json.dump(per_launch, open("data/curve_exit_scored.json","w"))
