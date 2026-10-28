import json, urllib.request, time, collections, statistics, sys

RPC = "https://base-rpc.publicnode.com"
PM  = "0x498581fF718922c3f8e6A244956aF099B2652b2b"
T_INIT = "0xdd466e674ea557f56295e2d0218a125ea4b4f0f6f3307b95f85e6110838d6438"
T_SWAP = None  # discovered: 3 topics / 6 words
QUOTES = {
  "0x0000000000000000000000000000000000000000":"ETH",
  "0x4200000000000000000000000000000000000006":"WETH",
  "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913":"USDC",
  "0x1111111111166b7fe7bd91427724b487980afc69":"ZORA",
}

def rpc(method, params, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(RPC, method="POST",
                data=json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params}).encode(),
                headers={"Content-Type":"application/json","User-Agent":"curl/8.4.0"})
            d = json.load(urllib.request.urlopen(req, timeout=30))
            if "error" in d: raise RuntimeError(d["error"])
            return d["result"]
        except Exception as e:
            if i == tries-1: raise
            time.sleep(1.5*(i+1))

def get_logs(frm, to, topic0=None):
    p = {"address": PM, "fromBlock": hex(frm), "toBlock": hex(to)}
    if topic0: p["topics"]=[topic0]
    return rpc("eth_getLogs",[p])

def words(data):
    d = data[2:]
    return [d[i:i+64] for i in range(0, len(d), 64)]

def s2i(w, bits):  # signed int from hex word
    v = int(w,16); return v-(1<<bits) if v >= (1<<(bits-1)) else v

head = int(rpc("eth_blockNumber",[]),16)
END   = head - 60          # leave 60 blocks (~120s) of forward data for the newest launch
START = END - 1500         # ~50 min of launches
print(f"head={head}  scan [{START},{END}]  ({(END-START)*2/60:.0f} min of chain)", file=sys.stderr)

inits, swaps = [], []
for a in range(START, END+1, 200):
    b = min(a+199, END)
    for l in get_logs(a,b):
        t0, nt, nw = l["topics"][0], len(l["topics"]), len(words(l["data"]))
        if t0 == T_INIT: inits.append(l)
        elif nt==3 and nw==6: swaps.append(l)
    print(f"  ..{b} inits={len(inits)} swaps={len(swaps)}", file=sys.stderr)
    time.sleep(0.25)

# index swaps by pool id
by_pool = collections.defaultdict(list)
for l in swaps:
    by_pool[l["topics"][1]].append(l)

print(f"\ninits={len(inits)}  swaps={len(swaps)}  pools_with_swaps={len(by_pool)}", file=sys.stderr)

rows=[]
for l in inits:
    pid = l["topics"][1]
    c0  = "0x"+l["topics"][2][-40:]
    c1  = "0x"+l["topics"][3][-40:]
    w   = words(l["data"])
    hook= "0x"+w[2][-40:]
    sqrtP0 = int(w[3],16)
    cblk, ctxi, ctx = int(l["blockNumber"],16), int(l["transactionIndex"],16), l["transactionHash"]
    if c0 in QUOTES and c1 in QUOTES: continue           # quote/quote pool, not a launch
    if c0 in QUOTES: quote, tok_is_c1 = QUOTES[c0], True
    elif c1 in QUOTES: quote, tok_is_c1 = QUOTES[c1], False
    else: continue                                        # no recognised quote leg
    if sqrtP0 == 0: continue

    def tokpx(sq):
        if sq==0: return None
        P = (sq/2**96)**2            # currency1 per currency0
        return (1.0/P) if tok_is_c1 else P   # token priced in quote

    sw = sorted(by_pool.get(pid,[]), key=lambda x:(int(x["blockNumber"],16), int(x["transactionIndex"],16), int(x["logIndex"],16)))
    path=[(cblk,ctxi,ctx,tokpx(sqrtP0),"init")]
    for s in sw:
        path.append((int(s["blockNumber"],16), int(s["transactionIndex"],16), s["transactionHash"],
                     tokpx(int(words(s["data"])[2],16)), "swap"))
    rows.append(dict(pid=pid, hook=hook, quote=quote, cblk=cblk, ctxi=ctxi, ctx=ctx,
                     tok_is_c1=tok_is_c1, path=path, nswaps=len(sw)))

json.dump(rows, open("data/base_v4_tape.json","w"))
print(f"launch rows written: {len(rows)}", file=sys.stderr)

# ---- headline: launchpad mix, and is the first buy ATOMIC with the create? ----
hk = collections.Counter((r["hook"][:10], r["quote"]) for r in rows)
print("\n=== launchpad (hook, quote) mix ===")
for k,n in hk.most_common(10): print(f"  {k[0]}  {k[1]:5s}  {n}")

atomic=same_blk=later=nosw=0
first_off=[]
for r in rows:
    if r["nswaps"]==0: nosw+=1; continue
    f = r["path"][1]
    if f[2] == r["ctx"]: atomic+=1
    elif f[0]==r["cblk"]: same_blk+=1; first_off.append(0)
    else: later+=1; first_off.append(f[0]-r["cblk"])
print(f"\n=== where is the FIRST swap? (n={len(rows)}) ===")
print(f"  ATOMIC (same tx as Initialize) : {atomic:4d}   <- unreachable seat")
print(f"  same block, LATER tx           : {same_blk:4d}   <- reachable by latency")
print(f"  a later block                  : {later:4d}   <- reachable, easier")
print(f"  never traded                   : {nosw:4d}")
if first_off: print(f"  median first-swap block offset (non-atomic): {statistics.median(first_off)}")
