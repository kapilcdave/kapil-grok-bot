import unittest

import pumpregime as R


class WindowIndexTest(unittest.TestCase):
    def test_snapshot_excludes_current_slot(self):
        rows = [
            {"slot": 90, "sol": 1_000_000_000, "buy": 1},
            {"slot": 99, "sol": 2_000_000_000, "buy": 0},
            {"slot": 100, "sol": 9_000_000_000, "buy": 1},
        ]
        index = R.WindowIndex(rows, [90, 99, 100])
        snapshot = index.snapshot(100, 25)
        self.assertEqual(snapshot["trades"], 2)
        self.assertEqual(snapshot["births"], 2)
        self.assertEqual(snapshot["buy"], 1.0)
        self.assertEqual(snapshot["sell"], 2.0)

    def test_regime_requires_resolved_history(self):
        index = R.WindowIndex([], [])
        self.assertIsNone(R.regime_features(index, 100, [{"pnl": 0.1}] * 19))
        features = R.regime_features(index, 100, [{"pnl": 0.1}] * 20)
        self.assertAlmostEqual(features["recent_mean_20"], 0.1)
        self.assertEqual(features["recent_win_20"], 1.0)


if __name__ == "__main__":
    unittest.main()
