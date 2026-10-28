"""pump.fun Anchor event decoding from processed `logsSubscribe` log lines.

Two corrections to the shape used elsewhere in this tree, both measured
2026-10-05 against the live program:

1. `pumptape.py` reads events from **inner instruction data**, where each payload
   is wrapped in the Anchor self-CPI discriminator `e445a52e51cb9a1d`. A
   `Program data:` LOG line is **not** wrapped -- its first 8 bytes are the event
   discriminator itself. Looking for the CPI wrapper in logs finds zero events,
   which is what made the log path look dead.
2. The log path is live and dense: 1,445 `TradeEvent` and 18 `CreateEvent` in
   25 s from the public endpoint. `pumptape.py`'s note that logs are unreliable
   is true of the CPI-wrapped form, not of the event form.

`CreateEvent` is the only free source of the token's **text** (name, symbol,
uri), which is the channel this arm exists to test.
"""
import base64
import hashlib
import struct

PUMP_FUN = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
LAMPORTS = 1e9
TOK_DP = 1e6
LOG_PREFIX = "Program data: "


def event_disc(name):
    """Anchor event discriminator. Verified: TradeEvent -> bddb7fd34ee661ee,
    which is the constant `pumptape.py` arrived at independently."""
    return hashlib.sha256(("event:" + name).encode()).digest()[:8]


TRADE_DISC = event_disc("TradeEvent")
CREATE_DISC = event_disc("CreateEvent")
COMPLETE_DISC = event_disc("CompleteEvent")

B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(raw):
    n = int.from_bytes(raw, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = B58[r] + out
    pad = len(raw) - len(raw.lstrip(b"\x00"))
    return "1" * pad + (out or "1")


def _borsh_str(raw, off, cap=512):
    length, = struct.unpack_from("<I", raw, off)
    off += 4
    if length > cap or off + length > len(raw):
        raise ValueError(f"borsh string length {length} out of range")
    return raw[off:off + length].decode("utf-8", "replace"), off + length


def parse_create(body):
    """CreateEvent -> dict, or None on layout drift.

    name, symbol, uri (borsh strings), mint, bonding_curve, user, creator,
    timestamp, virtual_token_reserves, virtual_sol_reserves.
    """
    try:
        off = 0
        name, off = _borsh_str(body, off, 256)
        symbol, off = _borsh_str(body, off, 64)
        uri, off = _borsh_str(body, off, 512)
        mint = body[off:off + 32]; off += 32
        curve = body[off:off + 32]; off += 32
        user = body[off:off + 32]; off += 32
        creator = body[off:off + 32]; off += 32
        ts, = struct.unpack_from("<q", body, off); off += 8
        v_tok, v_sol = struct.unpack_from("<QQ", body, off)
    except (struct.error, ValueError, IndexError):
        return None
    # Layout drift guards, not filters: a plausible timestamp and nonzero
    # reserves. A create whose reserves are zero cannot be priced.
    if not (1.6e9 < ts < 2.5e9) or not v_tok or not v_sol:
        return None
    return {"name": name, "symbol": symbol, "uri": uri,
            "mint": b58encode(mint), "curve": b58encode(curve),
            "user": b58encode(user), "creator": b58encode(creator),
            "ts": ts, "v_sol": v_sol / LAMPORTS, "v_tok": v_tok / TOK_DP}


def parse_trade(body):
    """TradeEvent -> dict, or None. Layout per `pumptape.parse_trade`, whose
    offsets were cross-checked against the virtual-reserve ratio."""
    if len(body) < 113:
        return None
    try:
        off = 0
        mint = body[off:off + 32]; off += 32
        sol, tok = struct.unpack_from("<QQ", body, off); off += 16
        is_buy = body[off]; off += 1
        user = body[off:off + 32]; off += 32
        ts, = struct.unpack_from("<q", body, off); off += 8
        v_sol, v_tok = struct.unpack_from("<QQ", body, off)
    except struct.error:
        return None
    if is_buy > 1 or not (1.6e9 < ts < 2.5e9):
        return None
    if not (sol and tok and v_sol and v_tok):
        # ~2.7% of events per pumptape: a zero-SOL leg has no price, so it can
        # neither fill nor be marked. Dropped, and the caller counts it.
        return None
    return {"mint": b58encode(mint), "user": b58encode(user), "ts": ts,
            "side": "buy" if is_buy else "sell",
            "sol": sol / LAMPORTS, "tok": tok / TOK_DP,
            "v_sol": v_sol / LAMPORTS, "v_tok": v_tok / TOK_DP}


def decode_logs(logs):
    """(creates, trades, counts) from one notification's log lines."""
    creates, trades = [], []
    counts = {"data_lines": 0, "undecodable": 0, "other_event": 0,
              "bad_trade": 0, "bad_create": 0}
    for line in logs or []:
        if not line.startswith(LOG_PREFIX):
            continue
        counts["data_lines"] += 1
        try:
            raw = base64.b64decode(line[len(LOG_PREFIX):], validate=False)
        except Exception:                                   # noqa: BLE001
            counts["undecodable"] += 1
            continue
        if len(raw) < 8:
            counts["undecodable"] += 1
            continue
        disc, body = raw[:8], raw[8:]
        if disc == TRADE_DISC:
            row = parse_trade(body)
            if row is None:
                counts["bad_trade"] += 1
            else:
                trades.append(row)
        elif disc == CREATE_DISC:
            row = parse_create(body)
            if row is None:
                counts["bad_create"] += 1
            else:
                creates.append(row)
        else:
            counts["other_event"] += 1
    return creates, trades, counts


def curve_price(v_sol, v_tok):
    """SOL per whole token, or None when the curve has no price."""
    if not v_sol or not v_tok or v_sol <= 0 or v_tok <= 0:
        return None
    return v_sol / v_tok


def buy_quote(v_sol, v_tok, sol_in):
    """Tokens received for `sol_in` through the constant product, charging our
    own impact. Returns (tokens_out, avg_price) or (None, None)."""
    if sol_in <= 0 or not v_sol or not v_tok:
        return None, None
    k = v_sol * v_tok
    new_sol = v_sol + sol_in
    tok_out = v_tok - k / new_sol
    if tok_out <= 0:
        return None, None
    return tok_out, sol_in / tok_out


def sell_quote(v_sol, v_tok, tok_in):
    """SOL received for `tok_in` through the constant product, charging our own
    impact. Returns (sol_out, avg_price) or (None, None)."""
    if tok_in <= 0 or not v_sol or not v_tok:
        return None, None
    k = v_sol * v_tok
    new_tok = v_tok + tok_in
    sol_out = v_sol - k / new_tok
    if sol_out <= 0:
        return None, None
    return sol_out, sol_out / tok_in
