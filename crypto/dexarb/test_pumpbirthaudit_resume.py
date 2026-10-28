import pumpbirthaudit_resume as R


def row(mint, sig):
    return {"mint": mint, "first_sig": sig}


def test_shards_are_stable_and_disjoint():
    rows = [row(f"m{i}", f"s{i}") for i in range(100)]
    left = {i for i, value in enumerate(rows) if R.shard_of(value, 2) == 0}
    right = {i for i, value in enumerate(rows) if R.shard_of(value, 2) == 1}
    assert left
    assert right
    assert left.isdisjoint(right)
    assert left | right == set(range(100))
