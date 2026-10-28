"""Closed-form pump.fun bonding-curve fills, with the fee calibrated from tape.

The curve is constant-product on virtual reserves, so the fill for any clip is
exact arithmetic -- there is no fill model and therefore no fill-model parameter
to sweep (contrast `the-paper-fill-model-caps-the-win-not-the-loss`).

Fee is NOT assumed, it is read out of the event. Measured on real tape:

* `sol_amount` is the CURVE-MOVING amount, not the buyer's total outlay. Solving
  the reserve update for an implied fee returns ~0.00000 on 21,578 buys and
  18,343 sells, which is the proof.
* the fee sits outside it, in the event's own fields:
  `fee_basis_points` (offset 161) is 95, and `fee` (offset 169) is exactly
  95.0 bps of `sol_amount`. `creator_fee_basis_points` (offset 209) is 0 or 30,
  putting the p90 total at 125 bps per side.

So moving the curve by x costs x(1+r) to buy and returns x(1-r) on a sell.
"""
import collections

LAMPORTS = 1e9
TOK_DP = 1e6
INITIAL_VTOK = 1_073_000_000_000_000     # measured modal virgin curve state


def pre_reserves(r):
    """Reserves immediately BEFORE trade r, ignoring fee (fee shifts vsol by
    f*size, which is <1e-5 of k for typical clips -- adequate for identifying a
    virgin curve, not for pricing)."""
    if r["buy"]:
        return r["vsol"] - r["sol"], r["vtok"] + r["tok"]
    return r["vsol"] + r["sol"], r["vtok"] - r["tok"]


def discover_initial_states(rows, min_count=3, rel_tol=1e-9):
    """Find the curve configurations actually present, instead of trusting one
    hardcoded constant.

    A virgin curve's pre-trade token reserve is the largest value that curve
    ever shows, and every launch of the same config shows the identical integer.
    So candidates are pre-trade reserves that (a) recur, and (b) sit at the top
    of the observed range. Returns a sorted list.

    Hardcoding a single initial state instead would silently drop every mint on
    any other config (`a-hardcoded-input-list-is-a-silent-sample-restriction`).
    """
    counts = {}
    for r in rows:
        _, vt = pre_reserves(r)
        if vt > 0:
            counts[vt] = counts.get(vt, 0) + 1
    if not counts:
        return [INITIAL_VTOK]
    top = max(counts)
    cands = [v for v, n in counts.items()
             if n >= min_count and v >= top * (1 - 1e-3)]
    if INITIAL_VTOK in counts and INITIAL_VTOK not in cands:
        cands.append(INITIAL_VTOK)
    return sorted(cands) or [top]


def is_virgin(r, states=(INITIAL_VTOK,), rel_tol=1e-9):
    """True iff r is the first-ever trade on its curve.

    Exact on real data: the chain computes tokens_out = vtok_pre - vtok_post in
    integers, so reconstruction is lossless. The tolerance only absorbs
    float round-trips in synthetic tests."""
    _, vtok_pre = pre_reserves(r)
    for s in states:
        if abs(vtok_pre - s) <= rel_tol * s:
            return True
    return False


def fee_estimates(rows):
    """Fee implied by the reserve update, ASSUMING sol_amount were gross.

    On real tape this returns ~0, which is the proof that sol_amount is the
    curve-moving amount and the fee sits outside it. Kept as that check; the
    fee actually charged is FEE_BPS, read from the event itself."""
    buy, sell = [], []
    for r in rows:
        A, B, s, tok = r["vsol"], r["vtok"], r["sol"], r["tok"]
        if r["buy"]:
            # 1 - f = A*tok / (s * (B + tok))
            den = s * (B + tok)
            if den <= 0:
                continue
            f = 1.0 - (A * tok) / den
            if -0.2 < f < 0.5:
                buy.append(f)
        else:
            if tok >= B:
                continue
            vtok_pre = B - tok
            vsol_pre = A * B / vtok_pre          # k conserved
            out = vsol_pre - A
            if out <= 0:
                continue
            f = 1.0 - s / out
            if -0.2 < f < 0.5:
                sell.append(f)
    buy.sort(); sell.sort()
    return buy, sell


def median(xs):
    if not xs:
        return float("nan")
    n = len(xs)
    return xs[n // 2] if n % 2 else 0.5 * (xs[n // 2 - 1] + xs[n // 2])


           # Measured from TradeEvent: fee_basis_points is 95, and the `fee`
           # field is exactly 95.0 bps of sol_amount (p90 125 bps, because
           # creator_fee_basis_points is 0 or 30). sol_amount is the
           # CURVE-MOVING amount, so the fee sits outside it.
FEE_BPS = 95
FEE_BPS_P90 = 125


def buy_fill(vsol, vtok, budget, r):
    """Spend a total budget of `budget` lamports. Returns (tok, vsol', vtok').

    `r` is charged on the curve-moving amount, not deducted from the budget:
    the buyer pays x(1+r) to move the curve by x, so x = budget/(1+r)."""
    k = vsol * vtok
    x = budget / (1.0 + r)
    vsol2 = vsol + x
    vtok2 = k / vsol2
    return vtok - vtok2, vsol2, vtok2


def sell_fill(vsol, vtok, tok_in, r):
    """Sell `tok_in` base units. Returns (sol_received_net, vsol', vtok').

    The curve pays out y and the venue takes r*y, so the seller nets y(1-r)."""
    k = vsol * vtok
    vtok2 = vtok + tok_in
    vsol2 = k / vtok2
    y = vsol - vsol2
    return y * (1.0 - r), vsol2, vtok2


def round_trip(vsol_in, vtok_in, vsol_out, vtok_out, clip_sol, fee):
    """Buy clip_sol at one curve state, sell all of it at another.

    Returns net SOL P&L (excluding transaction/priority cost, added by the
    caller from observed on-chain fees). The tape is held FIXED: our own trade
    does not perturb what everyone else did. That is optimistic in both legs
    and is why a negative result here is decisive."""
    clip = clip_sol * LAMPORTS
    tok, _, _ = buy_fill(vsol_in, vtok_in, clip, fee)
    if tok <= 0:
        return None
    out, _, _ = sell_fill(vsol_out, vtok_out, tok, fee)
    return (out - clip) / LAMPORTS


def mid(vsol, vtok):
    """Marginal curve price, SOL per whole token."""
    return (vsol / LAMPORTS) / (vtok / TOK_DP)
