import unittest

import pumpsameentry as S


class EntryTest(unittest.TestCase):
    def test_entry_is_after_all_birth_signature_events(self):
        value = {"tape": [
            {"slot": 100, "sig": "birth"},
            {"slot": 100, "sig": "birth"},
            {"slot": 100, "sig": "later"},
        ]}
        index, row = S.entry_after_birth_transaction(value)
        self.assertEqual(index, 1)
        self.assertEqual(row["sig"], "birth")


if __name__ == "__main__":
    unittest.main()
