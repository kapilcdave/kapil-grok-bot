import pumpgraphreport as R


def test_formatters_handle_missing_values():
    assert R.pct(None) == "n/a"
    assert R.number(None) == "n/a"
    assert R.pct(0.1234) == "+12.34%"
