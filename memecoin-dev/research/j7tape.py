"""Record j7tracker's live feed to a JSONL tape, every event verbatim plus our receive time.

The feed is social (X/TikTok/YouTube posts, profile changes, ai_suggestion, token_meta), not on-chain
trades: join it to a chain tape on `recv_ns` to measure how late a post is against what the chain did.
A catch-all handler keeps events the docs don't list.

    J7_ACCESS_TOKEN=eyJ... python -m research.j7tape [--out research/data] [--host nyc|dfw]

The token is the `__Secure-j7_at` cookie (short-lived JWT): when it expires the server sends
auth_error, the recorder logs the expiry and exits, and you start it again with a fresh one.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import socketio

from devbot.j7 import J7Error, expiry

HOSTS = {"nyc": "https://nyc.j7tracker.io", "dfw": "https://dfw.j7tracker.io"}


class Tape:
    def __init__(self, out: Path) -> None:
        self.out, self.lock, self.n = out, threading.Lock(), 0
        self.f, self.day = None, ""

    def write(self, event: str, data) -> None:
        recv_ns = time.time_ns()
        day = datetime.fromtimestamp(recv_ns / 1e9, timezone.utc).strftime("%Y%m%d")
        line = json.dumps({"recv_ns": recv_ns, "event": event, "data": data}, separators=(",", ":"), default=str)
        with self.lock:
            if day != self.day:
                if self.f:
                    self.f.close()
                self.f, self.day = open(self.out / f"j7tape-{day}.jsonl", "a", buffering=1), day
            self.f.write(line + "\n")
            self.n += 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).parent / "data"))
    ap.add_argument("--host", choices=sorted(HOSTS), default="nyc")
    args = ap.parse_args()
    token = os.environ.get("J7_ACCESS_TOKEN", "").strip()
    if not token:
        sys.exit("set J7_ACCESS_TOKEN to the __Secure-j7_at cookie value")
    try:
        left = expiry(token) - time.time()
    except J7Error as e:
        sys.exit(str(e))
    if left <= 0:
        sys.exit("that access token has already expired; copy a fresh __Secure-j7_at")
    print(f"token valid for {left / 60:.0f} more minutes")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tape, done = Tape(out), threading.Event()
    sio = socketio.Client(reconnection=True, reconnection_delay=2, reconnection_delay_max=30)

    @sio.event
    def connect():
        tape.write("_connect", {"host": args.host})
        sio.emit("user_connected", token)   # nothing arrives until this is sent, on every reconnect

    @sio.event
    def disconnect():
        tape.write("_disconnect", None)

    @sio.on("auth_error")
    def auth_error(e):
        tape.write("auth_error", e)
        print(f"j7tracker refused the session: {e}", file=sys.stderr)
        done.set()

    @sio.on("*")
    def anything(event, data=None):
        tape.write(event, data)

    sio.connect(HOSTS[args.host], transports=["websocket"], auth={"token": token}, wait_timeout=15)
    try:
        while not done.wait(60):
            print(f"{datetime.now(timezone.utc):%H:%M:%S}Z {tape.n} events", flush=True)
            if time.time() > expiry(token):
                tape.write("_token_expired", None)
                print("access token expired; restart with a fresh one", file=sys.stderr)
                break
    except KeyboardInterrupt:
        pass
    finally:
        sio.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
