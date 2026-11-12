import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import calc  # noqa: E402


class MedianTest(unittest.TestCase):
    def test_odd_count(self):
        self.assertEqual(calc.median([5, 1, 3]), 3)

    def test_even_count_uses_integer_mean_of_middles(self):
        self.assertEqual(calc.median([4, 1, 3, 2]), 2)

    def test_ignores_outliers(self):
        self.assertEqual(calc.median([2000, 2001, 2002, 4095, 0]), 2001)

    def test_does_not_mutate_input(self):
        values = [3, 1, 2]
        calc.median(values)
        self.assertEqual(values, [3, 1, 2])


class SpreadTest(unittest.TestCase):
    def test_spread(self):
        self.assertEqual(calc.spread([10, 40, 25]), 30)


if __name__ == "__main__":
    unittest.main()
