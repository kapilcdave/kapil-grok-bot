import pumpfunderpreindex as P


def transaction(transfers, signers):
    return {
        "meta": {"err": None, "innerInstructions": []},
        "transaction": {
            "message": {
                "accountKeys": [
                    {"pubkey": key, "signer": key in signers}
                    for key in signers | {
                        edge["source"] for edge in transfers
                    } | {
                        edge["destination"] for edge in transfers
                    }
                ],
                "instructions": [
                    {
                        "program": "system",
                        "parsed": {
                            "type": "transfer",
                            "info": edge,
                        },
                    }
                    for edge in transfers
                ],
            }
        },
    }


def test_ingest_aggregates_and_local_lookup_is_strictly_precreate(tmp_path):
    connection = P.open_db(tmp_path / "index.sqlite")
    tx = transaction([
        {"source": "f", "destination": "c", "lamports": 3_000_000},
        {"source": "f", "destination": "c", "lamports": 4_000_000},
    ], {"f"})
    assert P.ingest_transaction(
        connection,
        tx,
        signature="sig",
        slot=90,
        selected_funders={"f"},
    ) == 1
    connection.commit()
    assert P.lookup_edge(connection, "c", 100)["funders"][0] == {
        "address": "f",
        "lamports": 7_000_000,
        "is_signer": True,
    }
    assert P.lookup_edge(connection, "c", 90) is None


def test_ingest_rejects_unselected_non_signer_and_dust(tmp_path):
    connection = P.open_db(tmp_path / "index.sqlite")
    tx = transaction([
        {"source": "f", "destination": "a", "lamports": 4_999_999},
        {"source": "x", "destination": "b", "lamports": 10_000_000},
        {"source": "n", "destination": "c", "lamports": 10_000_000},
    ], {"f", "x"})
    assert P.ingest_transaction(
        connection,
        tx,
        signature="sig",
        slot=90,
        selected_funders={"f", "n"},
    ) == 0


def test_watched_destination_indexes_later_nonselected_funder(tmp_path):
    connection = P.open_db(tmp_path / "index.sqlite")
    selected = transaction([
        {"source": "good", "destination": "c", "lamports": 10_000_000},
    ], {"good"})
    later = transaction([
        {"source": "other", "destination": "c", "lamports": 20_000_000},
    ], {"other"})
    assert P.ingest_transaction(
        connection,
        selected,
        signature="selected",
        slot=80,
        selected_funders={"good"},
    ) == 1
    assert P.ingest_transaction(
        connection,
        later,
        signature="later",
        slot=90,
        selected_funders={"good"},
        watched_destinations={"c"},
    ) == 1
    assert (
        P.lookup_edge(connection, "c", 100)["funders"][0]["address"]
        == "other"
    )


def test_resolver_returns_cache_miss_without_rpc(tmp_path):
    connection = P.open_db(tmp_path / "index.sqlite")
    edge = P.PreindexedResolver(connection)(
        None,
        {"creator": "unknown", "create_slot": 100},
        0,
        1.0,
    )
    assert edge["status"] == "no_preindexed_funder"
    assert edge["lookup_source"] == "local_preindex"


def test_invalidator_coverage_fails_closed_until_current(tmp_path):
    connection = P.open_db(tmp_path / "index.sqlite")
    P.insert_edge(
        connection,
        signature="fund",
        slot=80,
        source="f",
        destination="c",
        lamports=10_000_000,
        source_is_signer=True,
    )
    resolver = P.PreindexedResolver(
        connection, require_invalidator_coverage=True
    )
    birth = {"creator": "c", "create_slot": 100}
    assert resolver(None, birth, 0, 1.0)["status"] == (
        "invalidation_coverage_missing"
    )
    P.mark_coverage(connection, "c", 99)
    assert resolver(None, birth, 0, 1.0)["status"] == (
        "direct_funder_found"
    )
