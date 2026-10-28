"""What can our RPC actually serve for pump.fun tape reconstruction?

Read-only. Prints capability verdicts only -- never the endpoint or any key.
Run before pumptape.py; the answers decide whether an oracle bound is even
computable, and over what window.

    python pumpprobe.py
"""
import json
import os
import time
import urllib.request
import urllib.error

# Canonical program IDs (public, on-chain).
PUMP_FUN = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMP_SWAP = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
RAYDIUM_V4 = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"

PUBLIC_RPC = "https://api.mainnet-beta.solana.com"


def load_env(path=None):
    """Read .env into a dict. Values are never printed by this module."""
    path = path or os.path.join(os.path.dirname(__file__), "..", "..", ".env")
    out = {}
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip("'\"")
    except FileNotFoundError:
        pass
    return out


def rpc(url, method, params, timeout=30):
    """One JSON-RPC call. Returns (result, error_string, elapsed_seconds)."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params}).encode()
    req = urllib.request.Request(
        url, body, {"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read())
    except urllib.error.HTTPError as e:
        return None, "HTTP %d" % e.code, time.time() - t0
    except Exception as e:                                # noqa: BLE001
        return None, type(e).__name__, time.time() - t0
    if "error" in d:
        msg = d["error"].get("message", "")[:90]
        return None, "RPC %s: %s" % (d["error"].get("code"), msg), time.time() - t0
    return d.get("result"), None, time.time() - t0


def block_at(url, slot):
    """getBlock with signatures only -- cheapest probe of archive depth."""
    return rpc(url, "getBlock", [slot, {
        "encoding": "json", "transactionDetails": "signatures",
        "rewards": False, "maxSupportedTransactionVersion": 0}])


def probe(label, url):
    print("\n=== %s ===" % label)

    head, err, dt = rpc(url, "getSlot", [{"commitment": "confirmed"}])
    if err:
        print("  getSlot FAILED (%s) -- endpoint unusable" % err)
        return None
    print("  reachable, head slot %d, %.0f ms" % (head, dt * 1000))

    # Archive depth. Slots are ~400ms, so 1 day ~= 216,000 slots. A skipped
    # slot returns a "was skipped" error, which is NOT an archive failure --
    # so walk forward a few slots before concluding the depth is unavailable.
    print("  archive depth (getBlock):")
    depth = {}
    for days, n in [(0.0, 300), (1.0, 216_000), (7.0, 1_512_000),
                    (30.0, 6_480_000), (90.0, 19_440_000)]:
        target = head - n
        ok, last = False, ""
        for bump in range(6):                 # skipped-slot tolerance
            res, err, dt = block_at(url, target + bump)
            if res:
                ok, last = True, "%d tx, %.0f ms" % (
                    len(res.get("signatures", [])), dt * 1000)
                break
            last = err
            if "skipped" not in (err or "").lower():
                break
        depth[days] = ok
        print("     %-7s ago (slot %-12d) %s  %s" % (
            ("%.0fd" % days) if days else "head", target,
            "OK  " if ok else "NO  ", last))

    # Can we enumerate pump.fun activity by address? This is the alternative to
    # walking blocks, and is far cheaper if supported.
    res, err, dt = rpc(url, "getSignaturesForAddress",
                       [PUMP_FUN, {"limit": 10}])
    if err:
        print("  getSignaturesForAddress(pump.fun): NO (%s)" % err)
    else:
        print("  getSignaturesForAddress(pump.fun): OK, %d sigs, %.0f ms"
              % (len(res), dt * 1000))

    # Rate limit: 8 sequential getSlot calls, no pause.
    lat, fails = [], 0
    for _ in range(8):
        _, err, dt = rpc(url, "getSlot", [{"commitment": "confirmed"}])
        lat.append(dt)
        fails += bool(err)
    lat.sort()
    print("  8 back-to-back getSlot: %d failed, median %.0f ms, max %.0f ms"
          % (fails, lat[len(lat) // 2] * 1000, lat[-1] * 1000))

    return {"head": head, "depth": depth}


def main():
    env = load_env()
    have = sorted(k for k in env if k.startswith(("SOLANA", "JUPITER")))
    print("env keys present (names only): %s" % ", ".join(have))

    url = env.get("SOLANA_RPC_URL", "").strip()
    results = {}
    if url:
        # Report only whether it is the public endpoint, never the URL itself.
        is_public = "api.mainnet-beta.solana.com" in url
        print("SOLANA_RPC_URL: set, %s"
              % ("PUBLIC endpoint (no archive, hard rate limits)" if is_public
                 else "private/paid provider"))
        results["configured"] = probe("configured SOLANA_RPC_URL", url)
    else:
        print("SOLANA_RPC_URL: not set")

    if not url or "api.mainnet-beta.solana.com" not in url:
        results["public"] = probe("public mainnet-beta (baseline)", PUBLIC_RPC)

    print("\n--- verdict ---")
    for name, r in results.items():
        if not r:
            continue
        d = r["depth"]
        reach = [k for k, v in sorted(d.items()) if v]
        print("%s: archive reaches %s"
              % (name, ("%.0f days" % max(reach)) if reach else "HEAD ONLY"))
    print("An oracle bound needs per-trade tape over the window it screens.")
    print("Head-only means live collection is the ONLY path: no backfill.")


if __name__ == "__main__":
    main()
