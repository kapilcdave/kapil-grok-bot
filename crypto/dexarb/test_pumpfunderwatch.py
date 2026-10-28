import pumpfunderpreindex as P
import pumpfunderwatch as W


class Rpc:
    def __init__(self, signatures, transactions):
        self.signatures = signatures
        self.transactions = transactions

    def call(self, method, params, **_kwargs):
        if method == "getSignaturesForAddress":
            return self.signatures, None
        return self.transactions[params[0]], None


def tx(source, destination, lamports):
    return {
        "meta": {"err": None, "innerInstructions": []},
        "transaction": {
            "message": {
                "accountKeys": [
                    {"pubkey": source, "signer": True},
                    {"pubkey": destination, "signer": False},
                ],
                "instructions": [{
                    "program": "system",
                    "parsed": {
                        "type": "transfer",
                        "info": {
                            "source": source,
                            "destination": destination,
                            "lamports": lamports,
                        },
                    },
                }],
            }
        },
    }


def test_poll_indexes_only_once(tmp_path):
    connection = P.open_db(tmp_path / "index.sqlite")
    rpc = Rpc(
        [{"signature": "s", "slot": 90, "err": None}],
        {"s": tx("f", "c", 10_000_000)},
    )
    first = W.poll_funder(connection, rpc, "f", 25)
    second = W.poll_funder(connection, rpc, "f", 25)
    assert first["edges"] == 1
    assert second["unseen"] == 0
    assert P.lookup_edge(connection, "c", 100) is not None


class Batch:
    def __init__(self):
        self.calls = []

    def batch_call(self, calls):
        self.calls.append(calls)
        if calls[0][0] == "getSignaturesForAddress":
            return [
                ([{"signature": "s", "slot": 90, "err": None}], None)
            ]
        return [(tx("f", "c", 10_000_000), None)]


def test_batch_cycle_fetches_and_indexes(tmp_path):
    connection = P.open_db(tmp_path / "index.sqlite")
    output = W.run_cycle(connection, Batch(), {"f"}, 10)
    assert output["rpc_mode"] == "batch"
    assert output["transactions"] == 1
    assert output["edges"] == 1
    assert P.lookup_edge(connection, "c", 100) is not None
