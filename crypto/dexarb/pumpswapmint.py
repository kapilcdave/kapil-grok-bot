"""Which PumpSwap pools are SOL-quoted? Read it from the pool account.

pumpswapinc.py needs the quote leg to put TVL and volume in real units, and the
swap EVENT does not carry it -- a full byte scan of 20,000 event bodies found the
WSOL mint at no offset in any discriminator. Only the pool key is in the event.
So the mint comes from the pool ACCOUNT, one batched getMultipleAccounts.

The offset of quote_mint inside the account is not assumed either: the account
bytes are searched for the WSOL mint and the winning offset is reported with its
share, the same way the reserve and pool-key offsets were established. If no
offset dominates, that is reported rather than papered over.

    python3 pumpswapmint.py --head 448205742 --blocks 900
"""
import argparse
import base64
import collections
import json
import sys

from pumpprobe import load_env, rpc
import pumpswap as S
import pumpswapr as R
import pumptape as T

WSOL_B58 = "So11111111111111111111111111111111111111112"
WSOL = T.b58decode(WSOL_B58)


def fetch_accounts(url, keys_b58, batch=100):
    """getMultipleAccounts over all pool keys; returns {b58: raw bytes}."""
    out = {}
    for i in range(0, len(keys_b58), batch):
        chunk = keys_b58[i:i + batch]
        r, err, _ = rpc(url, "getMultipleAccounts",
                        [chunk, {"encoding": "base64"}], timeout=60)
        if err or not r:
            print("  batch %d failed: %s" % (i, err))
            continue
        for k, acc in zip(chunk, r.get("value") or []):
            if not acc:
                continue
            data = (acc.get("data") or [None])[0]
            if not data:
                continue
            try:
                out[k] = base64.b64decode(data)
            except Exception:                                  # noqa: BLE001
                continue
        print("  fetched %d/%d pool accounts" % (len(out), len(keys_b58)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--blocks", type=int, default=900)
    ap.add_argument("--head", type=int, required=True)
    ap.add_argument("--cache", default="data/pumpswap_events.jsonl")
    ap.add_argument("--json", default="data/pumpswap_mints.json")
    args = ap.parse_args()
    env = load_env()
    url = env.get("SOLANA_RPC_URL", "").strip()
    if not url:
        sys.exit("SOLANA_RPC_URL not set")
    lo = args.head - 400 - args.blocks
    slots = list(range(lo, lo + args.blocks))
    evs = S.cached_events(url, slots, args.cache, offline=True)
    good, excl, dup, _n = R.series(evs, min_swaps=24)
    keys = {k: T.b58encode(k) for k in good}
    print("\nresolving the quote mint for %d pools that cleared the gate" % len(keys))
    accs = fetch_accounts(url, sorted(keys.values()))
    if not accs:
        sys.exit("no pool account fetched")
    lens = collections.Counter(len(v) for v in accs.values())
    print("  account sizes: %s" % dict(lens))

    # where does WSOL sit inside the account?
    hits = collections.Counter()
    for raw in accs.values():
        i = raw.find(WSOL)
        while i != -1:
            hits[i] += 1
            i = raw.find(WSOL, i + 1)
    print("\n=== WSOL mint offsets inside the pool account ===")
    for off, n in hits.most_common(6):
        print("  @%3d  %d of %d accounts (%.1f%%)"
              % (off, n, len(accs), 100 * n / len(accs)))
    if not hits:
        print("  WSOL appears in NO pool account. Either these pools are all")
        print("  non-SOL-quoted, or the pool key offset (@%d) is not the pool."
              % R.KEY_OFF)
        sys.exit(2)
    # The two winning offsets are 32 bytes apart, which is the signature of two
    # ADJACENT pubkey fields -- base_mint and quote_mint. Which of the two is
    # named "quote" cannot be read off the bytes, and guessing is how a decode
    # goes wrong quietly (`ignored-unknown-fields-make-a-wrong-name-look-unset`).
    # So do not guess: emit BOTH groups and let the income test report each
    # separately. If the verdict is the same in both, the ambiguity is
    # irrelevant and no resolution was ever needed.
    offs = sorted(o for o, _n in hits.most_common(2))
    groups = collections.defaultdict(list)
    for k58, raw in accs.items():
        at = tuple(o for o in offs if raw[o:o + 32] == WSOL)
        groups[at].append(k58)
    print("\n=== pools grouped by WHERE their WSOL leg sits ===")
    for at, ks in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        lab = ("WSOL at " + ", ".join("@%d" % o for o in at)) if at \
            else "NO WSOL leg at all -- reserves are NOT lamports"
        print("  %3d pools  %s" % (len(ks), lab))
    nowsol = groups.get((), [])
    if nowsol:
        print("\n  the %d pools with no WSOL leg are excluded from any income"
              % len(nowsol))
        print("  number: their reserve fields cannot be read as SOL.")
        ex = collections.Counter()
        for k58 in nowsol:
            raw = accs[k58]
            for o in offs:
                ex[T.b58encode(raw[o:o + 32])] += 1
        for m, n in ex.most_common(4):
            print("    %2d  %s" % (n, m))
    with open(args.json, "w") as fh:
        json.dump({"wsol_offsets": offs, "n_fetched": len(accs),
                   "groups": {(",".join(str(o) for o in at) or "none"): ks
                              for at, ks in groups.items()}}, fh, indent=1)
    print("\nwrote %s" % args.json)


if __name__ == "__main__":
    main()
