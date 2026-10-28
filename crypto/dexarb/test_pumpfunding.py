import pumpfunding as F


def tx(instructions, inner=None):
    return {
        "meta": {"err": None, "innerInstructions": inner or []},
        "transaction": {
            "message": {
                "accountKeys": [
                    {"pubkey": "payer", "signer": True},
                    {"pubkey": "recipient", "signer": False},
                ],
                "instructions": instructions,
            }
        },
    }


def transfer(source="payer", destination="recipient", lamports=123):
    return {
        "program": "system",
        "parsed": {
            "type": "transfer",
            "info": {
                "source": source,
                "destination": destination,
                "lamports": lamports,
            },
        },
    }


def test_parsed_system_transfers_reads_outer_and_inner():
    got = F.parsed_system_transfers(
        tx([transfer(lamports=10)], [{"instructions": [transfer(lamports=20)]}])
    )
    assert [x["lamports"] for x in got] == [10, 20]


def test_parsed_system_transfers_excludes_failed_transaction():
    value = tx([transfer()])
    value["meta"]["err"] = {"InstructionError": [0, "Failed"]}
    assert F.parsed_system_transfers(value) == []


def test_signer_set_uses_parsed_account_keys():
    assert F.signer_set(tx([])) == {"payer"}


def test_direct_funders_ignores_dust_but_aggregates_same_source():
    value = tx([
        transfer(lamports=2_000_000),
        transfer(lamports=3_000_000),
        transfer(source="dust", lamports=1),
    ])
    funders, _signers, dust_n, dust_lamports = F.direct_funders(value, "recipient")
    assert funders == [{
        "address": "payer",
        "lamports": 5_000_000,
        "is_signer": True,
    }]
    assert dust_n == 1
    assert dust_lamports == 1


def test_direct_funders_from_facts_reuses_decoded_transaction():
    transfers = [
        {"source": "payer", "destination": "recipient", "lamports": 5_000_000}
    ]
    got = F.direct_funders_from_facts(
        transfers, {"payer"}, "recipient"
    )
    assert got == (
        [{
            "address": "payer",
            "lamports": 5_000_000,
            "is_signer": True,
        }],
        {"payer"},
        0,
        0,
    )


def test_program_ids_reads_outer_and_inner():
    value = tx(
        [{"programId": "outer"}],
        [{"instructions": [{"programId": "inner"}]}],
    )
    assert F.program_ids(value) == ["inner", "outer"]


def test_summary_finds_common_funders():
    rows = [
        {
            "status": "direct_funder_found",
            "funders": [{"address": "common", "lamports": 1, "is_signer": True}],
            "slots_before_create": 1,
            "funding_lamports": 1_000_000_000,
            "error": None,
        },
        {
            "status": "direct_funder_found",
            "funders": [{"address": "common", "lamports": 2, "is_signer": False}],
            "slots_before_create": 5,
            "funding_lamports": 2_000_000_000,
            "error": None,
        },
        {
            "status": "no_direct_funder_in_history",
            "funders": [],
            "slots_before_create": None,
            "funding_lamports": 0,
            "error": None,
        },
    ]
    got = F.summarize(rows)
    assert got["direct_funder_rate"] == 2 / 3
    assert got["signer_source_rate"] == 1 / 3
    assert got["repeated_primary_funders"] == 1
    assert got["launches_from_repeated_primary_funders"] == 2
    assert got["slots_before_create"]["share_le_2"] == 0.5


def test_latest_rows_can_resume_funding_edge_file(tmp_path):
    path = tmp_path / "funding.jsonl"
    path.write_text(
        '{"mint":"m","create_sig":"c","status":"unresolved"}\n'
        '{"mint":"m","create_sig":"c","status":"direct_funder_found"}\n'
    )
    got = F.latest_rows(path, "create_sig")
    assert got[("m", "c")]["status"] == "direct_funder_found"
