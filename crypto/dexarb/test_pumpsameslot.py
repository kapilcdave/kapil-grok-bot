import unittest

import pumpsameslot as S


class SameSlotTest(unittest.TestCase):
    def test_classification_uses_signature(self):
        birth = {"slot": 100, "sig": "birth"}
        self.assertEqual(S.classify(birth, {"slot": 100, "sig": "birth"}),
                         "birth_transaction")
        self.assertEqual(S.classify(birth, {"slot": 100, "sig": "later"}),
                         "same_slot_later_transaction")
        self.assertEqual(S.classify(birth, {"slot": 101, "sig": "later"}),
                         "plus_1_slot")


if __name__ == "__main__":
    unittest.main()
