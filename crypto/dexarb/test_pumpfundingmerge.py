import json

import pumpfundingmerge as M


def row(status, error=None):
    return {
        "mint": "m",
        "create_sig": "c",
        "status": status,
        "error": error,
    }


def test_merged_latest_prefers_terminal_over_later_marker(tmp_path):
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    first.write_text(json.dumps(row("direct_funder_found")) + "\n")
    second.write_text(json.dumps(row("history_loaded")) + "\n")
    got = M.merged_latest([first, second])
    assert got[("m", "c")]["status"] == "direct_funder_found"


def test_merged_latest_prefers_clean_terminal_over_error(tmp_path):
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    first.write_text(json.dumps(row("unresolved", "throttled")) + "\n")
    second.write_text(json.dumps(row("no_direct_funder_in_history")) + "\n")
    got = M.merged_latest([first, second])
    assert got[("m", "c")]["error"] is None


def test_merge_reports_pending_without_claiming_complete(tmp_path, monkeypatch):
    path = tmp_path / "rows.jsonl"
    path.write_text(json.dumps(row("history_loaded")) + "\n")
    monkeypatch.setattr(
        M.F,
        "births",
        lambda *_args: [{"mint": "m", "create_sig": "c"}],
    )
    rows, coverage = M.merge([path], "audit.jsonl")
    assert len(rows) == 1
    assert coverage == {
        "expected_births": 1,
        "rows_present": 1,
        "missing": 0,
        "errors": 0,
        "pending": 1,
        "complete": False,
    }
