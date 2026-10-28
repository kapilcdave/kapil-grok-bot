"""Discover pump.fun's on-chain event encoding from real transactions.

Guessing an Anchor layout is how you get a plausible-looking wrong number, so
this dumps what is actually there first: which log lines pump.fun emits, the
discriminators present, and the byte length of each event payload. The decoder
in pumptape.py is written against THIS output, not against documentation.

    python3 pumpdecode.py            # inspect recent pump.fun txs
"""
import base64
import collections
import json
import struct
import sys

from pumpprobe import PUMP_FUN, load_env, rpc

# Anchor's "emit_cpi" wrapper discriminator: sha256("anchor:event")[:8].
CPI_EVENT_DISC = bytes.fromhex("e445a52e51cb9a1d")


def get_tx(url, sig):
    return rpc(url, "getTransaction", [sig, {
        "encoding": "json", "maxSupportedTransactionVersion": 0,
        "commitment": "confirmed"}], timeout=40)


def main():
    env = load_env()
    url = env.get("SOLANA_RPC_URL", "").strip()
    if not url:
        sys.exit("SOLANA_RPC_URL not set")

    sigs, err, _ = rpc(url, "getSignaturesForAddress", [PUMP_FUN, {"limit": 25}])
    if err:
        sys.exit("getSignaturesForAddress failed: %s" % err)
    print("pulled %d recent pump.fun signatures" % len(sigs))

    log_shapes = collections.Counter()
    payload_lens = collections.Counter()
    disc_seen = collections.Counter()
    samples = []

    for s in sigs:
        if s.get("err"):
            continue
        tx, err, _ = get_tx(url, s["signature"])
        if err or not tx:
            continue
        meta = tx.get("meta") or {}
        logs = meta.get("logMessages") or []

        for ln in logs:
            if ln.startswith("Program log: Instruction:"):
                log_shapes["ix:" + ln.split("Instruction:")[1].strip()] += 1
            elif ln.startswith("Program data:"):
                log_shapes["Program data"] += 1
                raw = base64.b64decode(ln.split("Program data:")[1].strip())
                payload_lens[len(raw)] += 1
                disc_seen[raw[:8].hex()] += 1
                if len(samples) < 6:
                    samples.append((s["signature"], raw))

        # Anchor CPI events live in inner instructions, not always in logs.
        for grp in meta.get("innerInstructions") or []:
            for ix in grp.get("instructions") or []:
                d = ix.get("data")
                if not d:
                    continue
                try:
                    raw = base58_decode(d)
                except Exception:                          # noqa: BLE001
                    continue
                if raw[:8] == CPI_EVENT_DISC:
                    log_shapes["cpi event"] += 1
                    payload_lens[len(raw)] += 1
                    disc_seen[raw[8:16].hex()] += 1
                    if len(samples) < 6:
                        samples.append((s["signature"], raw[8:]))

    print("\n--- log line shapes ---")
    for k, v in log_shapes.most_common(20):
        print("  %-40s %d" % (k, v))
    print("\n--- event payload byte lengths ---")
    for k, v in payload_lens.most_common(10):
        print("  %4d bytes   x%d" % (k, v))
    print("\n--- event discriminators ---")
    for k, v in disc_seen.most_common(10):
        print("  %s  x%d" % (k, v))

    print("\n--- candidate TradeEvent decode of longest payload ---")
    if samples:
        samples.sort(key=lambda t: -len(t[1]))
        sig, raw = samples[0]
        print("  sig %s, %d bytes" % (sig[:24] + "...", len(raw)))
        print("  hex %s" % raw.hex())
        try_trade_event(raw)


def base58_decode(s):
    alpha = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    n = 0
    for ch in s:
        n = n * 58 + alpha.index(ch)
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    pad = len(s) - len(s.lstrip("1"))
    return b"\x00" * pad + body


def try_trade_event(raw):
    """pump.fun TradeEvent is documented as:
        disc(8) mint(32) sol_amount(u64) token_amount(u64) is_buy(bool)
        user(32) timestamp(i64) virtual_sol_reserves(u64)
        virtual_token_reserves(u64) [+ real reserves / fee fields in newer IDL]
    = 8+32+8+8+1+32+8+8+8 = 113 minimum. Print the parse so a wrong layout is
    visible as an absurd number rather than silently believed."""
    if len(raw) < 113:
        print("  payload shorter than 113 bytes -- layout differs, stop")
        return
    off = 8
    mint = raw[off:off + 32]; off += 32
    sol, tok = struct.unpack_from("<QQ", raw, off); off += 16
    is_buy = raw[off]; off += 1
    user = raw[off:off + 32]; off += 32
    ts, = struct.unpack_from("<q", raw, off); off += 8
    vsol, vtok = struct.unpack_from("<QQ", raw, off); off += 16
    px = (vsol / 1e9) / (vtok / 1e6) if vtok else float("nan")
    print("  mint(b58 head) %s" % base58_encode(mint)[:12])
    print("  sol_amount     %d lamports (%.6f SOL)" % (sol, sol / 1e9))
    print("  token_amount   %d (%.3f tokens @6dp)" % (tok, tok / 1e6))
    print("  is_buy         %d %s" % (is_buy, "(1/0 expected)" if is_buy < 2
                                      else "<-- NOT A BOOL, layout is wrong"))
    print("  user(b58 head) %s" % base58_encode(user)[:12])
    print("  timestamp      %d %s" % (ts, "(plausible unix)" if 1.6e9 < ts < 2.1e9
                                      else "<-- IMPLAUSIBLE, layout is wrong"))
    print("  virt reserves  %.3f SOL / %.0f tokens" % (vsol / 1e9, vtok / 1e6))
    print("  implied price  %.3e SOL/token" % px)
    print("  trailing bytes %d (newer IDL adds fee/real-reserve fields)"
          % (len(raw) - off))


def base58_encode(b):
    alpha = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    n = int.from_bytes(b, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = alpha[r] + out
    return "1" * (len(b) - len(b.lstrip(b"\x00"))) + out


if __name__ == "__main__":
    main()
