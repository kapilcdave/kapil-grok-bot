import base64
import struct

import snipedecode as D


def test_discriminators_match_the_independently_derived_constant():
    # pumptape.py arrived at this hex by byte scanning a real block; deriving it
    # from sha256("event:TradeEvent") must agree or one of them is wrong.
    assert D.TRADE_DISC.hex() == "bddb7fd34ee661ee"
    assert D.CREATE_DISC.hex() == "1b72a94ddeeb6376"


def _create_body(name="Neura Pad", symbol="NPAD", uri="ipfs://cid",
                 ts=1791237379, v_tok=1_073_000_000_000_000, v_sol=30_000_000_000):
    out = b""
    for s in (name, symbol, uri):
        raw = s.encode()
        out += struct.pack("<I", len(raw)) + raw
    out += bytes(range(32)) * 1              # mint
    out += bytes(32)                         # bonding curve
    out += b"\x01" * 32                      # user
    out += b"\x02" * 32                      # creator
    out += struct.pack("<q", ts)
    out += struct.pack("<QQ", v_tok, v_sol)
    return out


def _trade_body(sol=1_000_000_000, tok=34_000_000_000_000, buy=1,
                ts=1791237379, v_sol=31_000_000_000, v_tok=1_040_000_000_000_000):
    body = (bytes(range(32)) + struct.pack("<QQ", sol, tok) + bytes([buy])
            + b"\x03" * 32 + struct.pack("<q", ts)
            + struct.pack("<QQ", v_sol, v_tok))
    # A real TradeEvent continues past the reserves with fee_recipient, the fee
    # basis points and the creator fee, so it is >= 113 bytes. The decoder's
    # length guard is calibrated to the real event and a short fixture would
    # test the guard instead of the layout.
    assert len(body) == 105
    return body + b"\x04" * 48


def _log(disc, body):
    return D.LOG_PREFIX + base64.b64encode(disc + body).decode()


def test_create_decodes_text_and_reserves():
    got = D.parse_create(_create_body())
    assert got["name"] == "Neura Pad" and got["symbol"] == "NPAD"
    assert got["uri"] == "ipfs://cid"
    # Scaled to whole units, not base units: the canonical opening state.
    assert got["v_sol"] == 30.0 and got["v_tok"] == 1_073_000_000.0


def test_create_rejects_a_drifted_layout_rather_than_guessing():
    # A timestamp outside the plausible band means the offsets moved.
    assert D.parse_create(_create_body(ts=42)) is None
    assert D.parse_create(_create_body(v_sol=0)) is None
    assert D.parse_create(b"\x00" * 8) is None


def test_trade_decodes_and_drops_a_zero_leg():
    got = D.parse_trade(_trade_body())
    assert got["side"] == "buy" and got["sol"] == 1.0
    assert D.parse_trade(_trade_body(buy=0))["side"] == "sell"
    # A zero-SOL leg has no price, so it can neither fill nor be marked.
    assert D.parse_trade(_trade_body(sol=0)) is None
    assert D.parse_trade(_trade_body(buy=7)) is None


def test_decode_logs_reads_the_event_disc_not_the_cpi_wrapper():
    logs = ["Program log: Instruction: Buy",
            _log(D.CREATE_DISC, _create_body()),
            _log(D.TRADE_DISC, _trade_body()),
            # The CPI-wrapped form, which is what inner instructions carry. A log
            # line is NOT wrapped, so this must land in `other_event`, not in a
            # silently mangled trade.
            _log(bytes.fromhex("e445a52e51cb9a1d"), D.TRADE_DISC + _trade_body())]
    creates, trades, counts = D.decode_logs(logs)
    assert len(creates) == 1 and len(trades) == 1
    assert counts["other_event"] == 1 and counts["data_lines"] == 3


def test_curve_round_trip_is_exact_with_no_other_flow():
    v_sol, v_tok = 30.0, 1_073_000_000.0
    tok, _ = D.buy_quote(v_sol, v_tok, 1.0)
    back, _ = D.sell_quote(v_sol + 1.0, v_tok - tok, tok)
    assert abs(back - 1.0) < 1e-9


def test_own_impact_rises_with_the_clip():
    # The whole point of pricing through the product: a bigger clip pays more
    # per token, so a clip is a real cost and not a scale factor.
    _, small = D.buy_quote(30.0, 1_073_000_000.0, 0.1)
    _, large = D.buy_quote(30.0, 1_073_000_000.0, 5.0)
    assert large > small * 1.1


def test_degenerate_curves_have_no_price():
    assert D.curve_price(0, 1) is None and D.curve_price(1, 0) is None
    assert D.buy_quote(30.0, 1e9, 0) == (None, None)
    assert D.sell_quote(30.0, 1e9, -1) == (None, None)
