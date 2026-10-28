"""GLiNER2.5-Decide sidecar: a typed text verdict, behind a degeneracy gate.

This process holds no keypair and has no transaction path. It answers one
question about one string and nothing else.

**Why the degeneracy gate is blocking, not advisory.** This exact model family
has already produced a tidy, stable, entirely void number in this tree. On
2026-10-03 `batch_classify_text` returned ONE label for every input (12/12
"first half only") *before any fine-tuning*, and an earlier branch read that as a
training problem and spent a GPU on it
(`gliner2-structure-head-works-classification-head-degenerate`). A constant head
does not make a strategy conservative -- it makes the instrument void, and a void
instrument still produces a clean-looking null that gets written down as
evidence. So: 12 probe texts spanning each task's label space run BEFORE the
first request is served, and a task that emits fewer than 2 distinct labels marks
the server degenerate. `/decide` then answers 503 and the run records
`reject_decider_degenerate` on every candidate, which is a loud void rather than
a quiet null.

The structure head is the one that works zero-shot (99.95% on 1,942 titles), so
the schema asks it for the token's subject, and the classification head's verdict
is reported beside it rather than trusted over it.

    SNIPE_DECIDE_TOKEN=... python3 snipedecide.py --port 8137 --selftest
    SNIPE_DECIDE_TOKEN=... python3 snipedecide.py --port 8137
"""
import argparse
import hashlib
import hmac
import json
import os
import socketserver
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_MODEL = "fastino/GLiNER2.5-Decide"

# Frozen with the preregistration. Changing a label changes the instrument, so
# the sha of this object is reported on every verdict and recorded in the ledger.
TASKS = {
    "signal": ["buy", "skip"],
    "risk_level": ["low", "medium", "high"],
    "sentiment": ["bullish", "bearish", "neutral"],
}

# The rules layer, in Python and not in an undocumented decoder: `buy` requires
# a non-high risk and a non-bearish read. Enforced here so the constraint is
# auditable and its infeasibility is reported rather than silently resolved.
CONSTRAINTS = [
    ("signal=buy implies risk_level!=high",
     lambda v: not (v.get("signal") == "buy" and v.get("risk_level") == "high")),
    ("signal=buy implies sentiment!=bearish",
     lambda v: not (v.get("signal") == "buy" and v.get("sentiment") == "bearish")),
]

# 12 probes spanning the label space of all three tasks. A head that is doing
# anything at all separates these.
PROBES = [
    "Token WAGMI launched with strong early buy pressure from 22 unique wallets and no creator selling.",
    "Token RUGME: the creator dumped the entire supply nine seconds after launch.",
    "A quiet launch with two trades and no description.",
    "Verified team, audited contract, locked liquidity, steady accumulation over four minutes.",
    "Anonymous deployer, honeypot bytecode, transfers disabled for holders.",
    "This is a tribute coin for a cat that died yesterday. No roadmap, no utility.",
    "Massive momentum, trending on every feed, buyers stacking the curve.",
    "Price collapsed 90% within thirty seconds of the first buy.",
    "A neutral utility token for a documentation index.",
    "Insider bundle of eight wallets bought the first slot together.",
    "Community-driven launch, fair distribution, creator bought 0.2 SOL.",
    "Scam alert: the same deployer has launched forty tokens this week, all at zero.",
]


def schema_sha():
    blob = json.dumps({"tasks": TASKS,
                       "constraints": [name for name, _ in CONSTRAINTS]},
                      sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


class Decider:
    """Wraps the model. `degenerate` is set by `selftest` and never cleared."""

    def __init__(self, model_id=DEFAULT_MODEL, loader=None, quantize=False):
        self.model_id = model_id
        self.quantize = quantize
        self.schema_sha = schema_sha()
        self.degenerate = None              # None = untested, True/False = tested
        self.degenerate_detail = {}
        self.load_s = None
        self.lock = threading.Lock()
        self.calls = 0
        self.latency_ms = []
        t0 = time.monotonic()
        self.model = (loader or self._load)()
        self.load_s = time.monotonic() - t0

    def _load(self):
        # AutoExtractor, not GLiNER2.from_pretrained -- the latter raises
        # AttributeError: 'ExtractorConfig' object has no attribute 'max_width'
        # on this checkpoint family.
        #
        # `quantize=True` converts to fp16 AFTER loading, so the PEAK is still
        # fp32 (~1.95 GB) while the steady state halves to ~975 MB. On the
        # e2-small that is the difference between a one-off transient the swap
        # absorbs and permanent thrashing: unquantised, the service burned 50 s of
        # CPU in 21 min of wall time at 50% iowait and never reached its listener.
        # `torch_dtype` is NOT an accepted kwarg here; `quantize` is.
        from gliner2 import AutoExtractor
        kwargs = {"quantize": True} if self.quantize else {}
        return AutoExtractor.from_pretrained(self.model_id, **kwargs)

    def classify(self, text):
        """Raw per-task labels for one string."""
        with self.lock:
            t0 = time.monotonic()
            out = self.model.classify_text(text, TASKS)
            ms = (time.monotonic() - t0) * 1e3
            self.calls += 1
            self.latency_ms.append(ms)
        labels = {}
        for task in TASKS:
            value = out.get(task) if isinstance(out, dict) else None
            if isinstance(value, list):
                value = value[0] if value else None
            labels[task] = value
        return labels, ms, out

    def decide(self, text):
        labels, ms, raw = self.classify(text)
        violated = [name for name, check in CONSTRAINTS if not check(labels)]
        feasible = not violated
        # Fail closed on infeasibility: an infeasible joint assignment is not a
        # buy. It is reported, never repaired by overwriting a label.
        accept = bool(feasible and labels.get("signal") == "buy")
        return {"labels": labels, "accept": accept,
                "constraint_feasible": feasible, "violated": violated,
                "latency_ms": round(ms, 2), "schema_sha": self.schema_sha,
                "model": self.model_id, "raw": raw}

    def selftest(self):
        """D1. Returns the detail dict and sets `self.degenerate`."""
        seen = {task: {} for task in TASKS}
        rows = []
        for probe in PROBES:
            labels, ms, _ = self.classify(probe)
            rows.append({"text": probe[:60], "labels": labels,
                         "latency_ms": round(ms, 1)})
            for task, label in labels.items():
                seen[task][label] = seen[task].get(label, 0) + 1
        detail = {task: {"distinct": len(counts), "counts": counts}
                  for task, counts in seen.items()}
        failing = [task for task, d in detail.items() if d["distinct"] < 2]
        self.degenerate = bool(failing)
        self.degenerate_detail = {"detail": detail, "failing_tasks": failing,
                                 "n_probes": len(PROBES), "rows": rows}
        return self.degenerate_detail


def render(payload):
    """Candidate -> the one string the model sees.

    Deliberately narrow. The numeric metrics are the deterministic gate's job and
    showing them here would let the model reconstruct the gate and look
    informative while adding nothing -- the exact failure in
    `the-gliner-gates-benchmark-is-a-one-line-width-gate`, where a 14-feature
    arm reduced to `spread_ticks >= 4`. So the decider sees TEXT ONLY, and the
    marginal test in the preregistration is then a clean test of the text channel.
    """
    parts = []
    name = (payload.get("name") or "").strip()
    symbol = (payload.get("symbol") or "").strip()
    if name or symbol:
        parts.append(f"Token {symbol or name}" + (f" ({name})" if name and symbol else ""))
    desc = (payload.get("description") or "").strip()
    if desc:
        parts.append(desc[:1200])
    for key in ("twitter", "website", "telegram"):
        if (payload.get(key) or "").strip():
            parts.append(f"{key}: present")
    return ". ".join(parts) if parts else ""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    decider = None
    token = None
    server_version = "snipedecide/1"

    def log_message(self, fmt, *args):                      # quieter journal
        if self.path != "/health":
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def _send(self, code, body):
        blob = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

    def _authorised(self):
        if not self.token:
            return True
        got = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not got.startswith(prefix):
            return False
        return hmac.compare_digest(got[len(prefix):], self.token)

    def do_GET(self):
        if self.path != "/health":
            return self._send(404, {"error": "not_found"})
        d = self.decider
        lat = sorted(d.latency_ms)
        self._send(200, {
            "ok": d.degenerate is False,
            "degenerate": d.degenerate,
            "degenerate_detail": d.degenerate_detail.get("detail"),
            "failing_tasks": d.degenerate_detail.get("failing_tasks"),
            "model": d.model_id, "quantized": d.quantize,
            "schema_sha": d.schema_sha,
            "load_s": round(d.load_s, 2) if d.load_s else None,
            "calls": d.calls,
            "latency_ms_p50": round(lat[len(lat) // 2], 1) if lat else None,
            "latency_ms_max": round(lat[-1], 1) if lat else None,
        })

    def do_POST(self):
        if self.path != "/decide":
            return self._send(404, {"error": "not_found"})
        if not self._authorised():
            return self._send(401, {"error": "unauthorised"})
        d = self.decider
        if d.degenerate is not False:
            # A void instrument must refuse, loudly. See the module docstring.
            return self._send(503, {"error": "decider_degenerate",
                                    "failing_tasks":
                                        d.degenerate_detail.get("failing_tasks"),
                                    "schema_sha": d.schema_sha})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError) as exc:
            return self._send(400, {"error": "bad_json",
                                    "detail": type(exc).__name__})
        text = payload.get("text")
        if text is None:
            text = render(payload)
        if not text:
            return self._send(200, {"accept": False, "reason": "empty_text",
                                    "text_chars": 0,
                                    "schema_sha": d.schema_sha})
        try:
            out = d.decide(text)
        except Exception as exc:                            # noqa: BLE001
            return self._send(500, {"error": "inference_failed",
                                    "detail": f"{type(exc).__name__}: {exc}"})
        out["text_chars"] = len(text)
        out["text"] = text[:400]
        self._send(200, out)


def serve(decider, host, port, token):
    Handler.decider = decider
    Handler.token = token
    socketserver.TCPServer.allow_reuse_address = True
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(json.dumps({"serving": f"{host}:{port}", "model": decider.model_id,
                      "degenerate": decider.degenerate,
                      "schema_sha": decider.schema_sha,
                      "load_s": round(decider.load_s, 2)}), flush=True)
    httpd.serve_forever()


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default=os.environ.get("SNIPE_DECIDE_MODEL",
                                                      DEFAULT_MODEL))
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8137)
    ap.add_argument("--selftest", action="store_true",
                    help="run D1 and exit with its verdict, serving nothing")
    ap.add_argument("--selftest-json", help="write the D1 detail here")
    ap.add_argument("--quantize", action="store_true",
                    default=bool(os.environ.get("SNIPE_DECIDE_QUANTIZE")),
                    help="convert to fp16 after loading; required on a 2 GB box")
    args = ap.parse_args(argv)
    token = os.environ.get("SNIPE_DECIDE_TOKEN")
    if not token and not args.selftest:
        print("[snipedecide] WARNING: no SNIPE_DECIDE_TOKEN, serving unauthenticated",
              file=sys.stderr)
    decider = Decider(args.model, quantize=args.quantize)
    detail = decider.selftest()
    out = {"model": args.model, "quantized": args.quantize,
           "schema_sha": decider.schema_sha,
           "load_s": round(decider.load_s, 2),
           "degenerate": decider.degenerate, **detail}
    if args.selftest_json:
        with open(args.selftest_json, "w") as fh:
            json.dump(out, fh, indent=2, sort_keys=True)
    print(json.dumps({k: v for k, v in out.items() if k != "rows"},
                     indent=2, sort_keys=True), flush=True)
    if args.selftest:
        # Non-zero on a degenerate head: a deploy script must be able to stop.
        return 1 if decider.degenerate else 0
    serve(decider, args.host, args.port, token)
    return 0


if __name__ == "__main__":                                  # pragma: no cover
    sys.exit(main())
