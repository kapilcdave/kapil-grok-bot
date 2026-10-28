from io import StringIO

import pumpfunding_batch as A


def test_transaction_call_requests_parsed_finalized_transaction():
    method, params = A.transaction_call("sig")
    assert method == "getTransaction"
    assert params[0] == "sig"
    assert params[1]["encoding"] == "jsonParsed"
    assert params[1]["commitment"] == "finalized"


def test_empty_result_has_funding_schema():
    birth = {
        "mint": "m",
        "creator": "c",
        "create_sig": "s",
        "create_slot": 10,
        "window": 1,
    }
    got = A.empty_result(birth)
    assert got["status"] == "unresolved"
    assert got["funders"] == []
    assert got["transaction_signers"] == []


def test_history_fetch_retries_nonterminal_rows_but_resumes_markers():
    assert A.needs_history_fetch(None)
    assert A.needs_history_fetch({"status": "unresolved", "error": None})
    assert A.needs_history_fetch({"status": "history_loaded", "error": "rpc"})
    assert not A.needs_history_fetch({
        "status": "history_loaded", "error": None
    })
    assert not A.needs_history_fetch({
        "status": "direct_funder_found", "error": None
    })


class FakeRpc:
    def __init__(self, with_transfer=True):
        self.next_id = 1
        self.http_calls = 0
        self.rpc_calls = 0
        self.retries = 0
        self.with_transfer = with_transfer

    def call(self, calls):
        out = {}
        for method, _params in calls:
            request_id = self.next_id
            self.next_id += 1
            self.rpc_calls += 1
            if method == "getSignaturesForAddress":
                result = [{"signature": "funding", "slot": 5, "err": None}]
            else:
                instructions = []
                if self.with_transfer:
                    instructions.append({
                        "program": "system",
                        "programId": "11111111111111111111111111111111",
                        "parsed": {
                            "type": "transfer",
                            "info": {
                                "source": "funder",
                                "destination": "creator",
                                "lamports": 5_000_000,
                            },
                        },
                    })
                result = {
                    "meta": {"err": None, "innerInstructions": []},
                    "transaction": {
                        "message": {
                            "accountKeys": [
                                {"pubkey": "funder", "signer": True},
                                {"pubkey": "creator", "signer": False},
                            ],
                            "instructions": instructions,
                        }
                    },
                }
            out[request_id] = {"id": request_id, "result": result}
        self.http_calls += 1
        return out


class SharedTransactionRpc(FakeRpc):
    def call(self, calls):
        if calls and calls[0][0] == "getSignaturesForAddress":
            out = {}
            for _method, _params in calls:
                request_id = self.next_id
                self.next_id += 1
                self.rpc_calls += 1
                out[request_id] = {
                    "id": request_id,
                    "result": [
                        {"signature": "shared-funding", "slot": 5, "err": None}
                    ],
                }
            self.http_calls += 1
            return out
        return super().call(calls)


class RecordingRpc(FakeRpc):
    def __init__(self):
        super().__init__()
        self.batch_lengths = []

    def call(self, calls):
        self.batch_lengths.append(len(calls))
        return super().call(calls)


def test_global_scan_finds_signer_funder():
    birth = {
        "mint": "m",
        "creator": "creator",
        "create_sig": "create",
        "create_slot": 10,
        "window": 1,
    }
    sink = StringIO()
    done = {}
    A.scan_global(FakeRpc(), [birth], 2, 10, sink, done)
    row = done[("m", "create")]
    assert row["status"] == "direct_funder_found"
    assert row["funders"] == [{
        "address": "funder", "lamports": 5_000_000, "is_signer": True
    }]
    assert row["slots_before_create"] == 5


def test_global_scan_fetches_shared_transaction_once():
    births = [
        {
            "mint": f"m{i}",
            "creator": "creator",
            "create_sig": f"create{i}",
            "create_slot": 10,
            "window": 1,
        }
        for i in range(2)
    ]
    rpc = SharedTransactionRpc()
    sink = StringIO()
    done = {}
    A.scan_global(rpc, births, 2, 10, sink, done)
    assert all(row["status"] == "direct_funder_found" for row in done.values())
    assert rpc.rpc_calls == 3


def test_global_scan_uses_separate_transaction_batch_size():
    births = [
        {
            "mint": f"m{i}",
            "creator": f"creator{i}",
            "create_sig": f"create{i}",
            "create_slot": 10,
            "window": 1,
        }
        for i in range(3)
    ]

    class DistinctHistoryRpc(RecordingRpc):
        def call(self, calls):
            if calls and calls[0][0] == "getSignaturesForAddress":
                out = {}
                for _method, params in calls:
                    request_id = self.next_id
                    self.next_id += 1
                    self.rpc_calls += 1
                    out[request_id] = {
                        "id": request_id,
                        "result": [{
                            "signature": "funding-" + params[0],
                            "slot": 5,
                            "err": None,
                        }],
                    }
                self.http_calls += 1
                self.batch_lengths.append(len(calls))
                return out
            return super().call(calls)

    rpc = DistinctHistoryRpc()
    A.scan_global(
        rpc,
        births,
        2,
        1,
        StringIO(),
        {},
        transaction_batch_size=3,
    )
    assert rpc.batch_lengths[-1] == 3


def test_global_scan_marks_exhausted_history_without_funder():
    birth = {
        "mint": "m",
        "creator": "creator",
        "create_sig": "create",
        "create_slot": 10,
        "window": 1,
    }
    sink = StringIO()
    done = {}
    A.scan_global(FakeRpc(with_transfer=False), [birth], 2, 10, sink, done)
    assert done[("m", "create")]["status"] == "no_direct_funder_in_history"


def test_global_scan_can_resume_persisted_history_without_refetching():
    birth = {
        "mint": "m",
        "creator": "creator",
        "create_sig": "create",
        "create_slot": 10,
        "window": 1,
    }
    marker = A.empty_result(birth)
    marker.update({
        "status": "history_loaded",
        "history": [{"signature": "funding", "slot": 5, "err": None}],
        "history_signatures": 1,
    })
    rpc = FakeRpc()
    sink = StringIO()
    done = {("m", "create"): marker}
    A.scan_global(
        rpc, [], 2, 10, sink, done, resumed=[(birth, marker)]
    )
    assert done[("m", "create")]["status"] == "direct_funder_found"
    assert rpc.http_calls == 1
