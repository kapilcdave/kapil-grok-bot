"""Pre-trade sell simulator for BSC launches (eth_simulateV1).

Simulates balanceOf -> buy -> balanceOf -> approve -> sell -> balanceOf as ONE
carried-state sequence and reports round-trip WBNB recovery at our own clip size.
Read-only: nothing is signed or broadcast.
"""
import json, urllib.request, time, itertools

RPC="https://bsc-rpc.publicnode.com"
ROUTER="0x10ED43C718714eb63d5aA57B78B54704E256024E"
WBNB  ="0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
ME    ="0x7a5c9b1e3f04d8aa2b6c1d0e9f8a7b6c5d4e3f21"   # fresh, no history
SEL_BUY ="0xb6f9de95"  # swapExactETHForTokensSupportingFeeOnTransferTokens
SEL_SELL="0x5c11d795"  # swapExactTokensForTokensSupportingFeeOnTransferTokens
SEL_APPROVE="0x095ea7b3"
SEL_BAL ="0x70a08231"
SEL_T0  ="0x0dfe1681"
SEL_T1  ="0xd21220a7"
MAXU="f"*64
w=lambda x: hex(x)[2:].rjust(64,"0")
a=lambda x: x.lower().replace("0x","").rjust(64,"0")

def rpc(m,p,tries=5):
    last=None
    for i in range(tries):
        try:
            r=urllib.request.Request(RPC,method="POST",
                data=json.dumps({"jsonrpc":"2.0","id":1,"method":m,"params":p}).encode(),
                headers={"Content-Type":"application/json","User-Agent":"curl/8.4.0"})
            d=json.load(urllib.request.urlopen(r,timeout=40))
            if "error" in d: raise RuntimeError(d["error"])
            return d["result"]
        except Exception as e:
            last=e; time.sleep(0.6*(i+1))
    raise last

def buy_data(tok,dl):  return SEL_BUY + w(0)+w(0x80)+a(ME)+w(dl) + w(2)+a(WBNB)+a(tok)
def sell_data(tok,amt,dl): return SEL_SELL + w(amt)+w(0)+w(0xa0)+a(ME)+w(dl) + w(2)+a(tok)+a(WBNB)

def check(tok, clip_bnb=0.01, label=""):
    clip=int(clip_bnb*1e18); dl=int(time.time())+3600
    o={"token":tok,"label":label,"clip_bnb":clip_bnb,"verdict":"?"}
    def run(calls):
        return rpc("eth_simulateV1",[{"blockStateCalls":[{
            "stateOverrides":{ME:{"balance":hex(clip*200)}},"calls":calls}],
            "validation":False,"traceTransfers":False,"returnFullTransactions":False},"latest"])[0]["calls"]
    G=hex(4_000_000); g=hex(400_000)
    try:
        c=run([{"from":ME,"to":tok,"data":SEL_BAL+a(ME),"gas":g},
               {"from":ME,"to":ROUTER,"value":hex(clip),"data":buy_data(tok,dl),"gas":G},
               {"from":ME,"to":tok,"data":SEL_BAL+a(ME),"gas":g}])
    except Exception as e:
        o["verdict"]="SIM_FAIL"; o["err"]=str(e)[:90]; return o
    if c[1].get("status")!="0x1":
        o["verdict"]="BUY_REVERTS"; o["err"]=str(c[1].get("error"))[:70]; return o
    if c[0].get("status")!="0x1" or c[2].get("status")!="0x1":
        o["verdict"]="NO_BALANCEOF"; return o
    got=int(c[2]["returnData"],16)-int(c[0]["returnData"],16)
    o["tokens_bought"]=got
    if got<=0: o["verdict"]="BUY_YIELDS_ZERO"; return o
    try:
        c=run([{"from":ME,"to":tok,"data":SEL_BAL+a(ME),"gas":g},
               {"from":ME,"to":ROUTER,"value":hex(clip),"data":buy_data(tok,dl),"gas":G},
               {"from":ME,"to":tok,"data":SEL_APPROVE+a(ROUTER)+MAXU,"gas":g},
               {"from":ME,"to":WBNB,"data":SEL_BAL+a(ME),"gas":g},
               {"from":ME,"to":ROUTER,"data":sell_data(tok,got,dl),"gas":G},
               {"from":ME,"to":WBNB,"data":SEL_BAL+a(ME),"gas":g}])
    except Exception as e:
        o["verdict"]="SIM_FAIL_SELL"; o["err"]=str(e)[:90]; return o
    o["approve_ok"]=c[2].get("status")=="0x1"
    if not o["approve_ok"]: o["verdict"]="APPROVE_REVERTS"; return o
    if c[4].get("status")!="0x1":
        o["verdict"]="SELL_REVERTS"; o["err"]=str(c[4].get("error"))[:70]; return o
    if c[3].get("status")!="0x1" or c[5].get("status")!="0x1":
        o["verdict"]="NO_WBNB_BAL"; return o
    out=int(c[5]["returnData"],16)-int(c[3]["returnData"],16)
    o["wbnb_out"]=out; o["recovery"]=out/clip
    o["verdict"]="SELLABLE" if out>0 else "SELL_YIELDS_ZERO"
    return o

if __name__=="__main__":
    USDT="0x55d398326f99059ff775485246999027b3197955"
    CAKE="0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82"
    ETH ="0x2170ed0880ac9a755fd29b2688956bd959f933f8"
    print("=== CALIBRATION: deep liquid tokens must recover ~99.5% (two 0.25% fees) ===")
    for t,lab in [(USDT,"USDT"),(CAKE,"CAKE"),(ETH,"ETH")]:
        r=check(t,0.01,lab)
        rec=r.get("recovery")
        print(f"  {lab:6s} verdict={r['verdict']:16s} tokens={r.get('tokens_bought')} "
              f"recovery={'%.4f'%rec if rec is not None else '-':>8s}  {r.get('err','')}")
    print("\n  (recovery = WBNB returned / BNB spent, same block, our clip size)")
