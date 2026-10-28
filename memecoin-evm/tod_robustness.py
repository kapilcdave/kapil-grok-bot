import json, urllib.request, time, collections, statistics, sys, datetime
RPCS=["https://bsc-dataseed.bnbchain.org","https://bsc-dataseed1.defibit.io","https://bsc-rpc.publicnode.com","https://bsc-dataseed2.bnbchain.org"]
import itertools
_rr=itertools.cycle(RPCS); TM="0x5c952063c7fc8610FFDB798152D69F0B9550762b"
T_CREATE="0x396d5e902b675b032348d3d2e9517ee8f0c4a926603fbc075d3d282ff00cad20"
TRADE={"0x0a5575b3648bae2210cee56bf33254cc1ddfbc7bf637c0af2ac18b14fb1bae19",
       "0x7db52723a3b2cdd6164364b3b766e65e540d7be48ffa89582956d8eaebe62942"}
def rpc(m,p,tries=5):
    for i in range(tries):
        try:
            RPC=next(_rr)
            r=urllib.request.Request(RPC,method="POST",data=json.dumps({"jsonrpc":"2.0","id":1,"method":m,"params":p}).encode(),
                headers={"Content-Type":"application/json","User-Agent":"curl/8.4.0"})
            d=json.load(urllib.request.urlopen(r,timeout=45))
            if "error" in d: raise RuntimeError(d["error"])
            return d["result"]
        except Exception as e:
            if i==tries-1: raise
            time.sleep(3*(i+1))
def W(d): d=d[2:]; return [d[i:i+64] for i in range(0,len(d),64)]

head=int(rpc("eth_blockNumber",[]),16)
BPH=int(3600/0.75)  # blocks per hour
for back_h,label in [(0,"now (US eve)"),(6,"-6h (Asia AM)"),(12,"-12h (Asia peak)"),(20,"-20h (EU AM)")]:
    END=head-200-back_h*BPH; START=END-4000
    try:
        ts=int(rpc("eth_getBlockByNumber",[hex(END),False])["timestamp"],16)
        utc=datetime.datetime.utcfromtimestamp(ts).strftime("%m-%d %H:%M")
        creates={}; tr=collections.Counter(); bnb=collections.Counter(); atom=0; ntr=0
        bytok=collections.defaultdict(list)
        for a in range(START,END+1,400):
            for l in rpc("eth_getLogs",[{"address":TM,"fromBlock":hex(a),"toBlock":hex(min(a+399,END))}]):
                t0=l["topics"][0]; w=W(l["data"])
                if t0==T_CREATE: creates["0x"+w[1][-40:]]=l["transactionHash"]
                elif t0 in TRADE:
                    tok="0x"+w[0][-40:]; tr[tok]+=1; bnb[tok]+=int(w[4],16)/1e18
                    bytok[tok].append(l["transactionHash"])
            time.sleep(0.6)
        hrs=(END-START)*0.75/3600
        n=len(creates)
        if n:
            for tok,ctx in creates.items():
                if bytok[tok]:
                    ntr+=1
                    if bytok[tok][0]==ctx: atom+=1
            vols=sorted(bnb[t] for t in creates)
            live=sum(1 for v in vols if v>0.1)
            print(f"{label:16s} {utc} UTC | launches/hr={n/hrs:5.0f} | traded={100*ntr/n:4.0f}% | "
                  f"1st-trade atomic={100*atom/max(ntr,1):4.0f}% | >0.1 BNB vol: {live:3d}/{n} ({100*live/n:4.1f}%) | med vol={statistics.median(vols):.4f}")
        else:
            print(f"{label:16s} {utc} UTC | no creates")
    except Exception as e:
        print(f"{label:16s} FAILED: {str(e)[:90]}")
