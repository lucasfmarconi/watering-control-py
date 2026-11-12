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


class SoilErrorTest(unittest.TestCase):
    def test_valid(self):
        self.assertIsNone(calc.soil_error(2000, 30, 250))

    def test_rails(self):
        self.assertEqual(calc.soil_error(10, 0, 250), "rail-low")
        self.assertEqual(calc.soil_error(4095, 0, 250), "rail-high")

    def test_noisy(self):
        self.assertEqual(calc.soil_error(2000, 400, 250), "noisy")


class RawToPercentTest(unittest.TestCase):
    def test_endpoints(self):
        self.assertEqual(calc.raw_to_percent(2800, 2800, 1200), 0)
        self.assertEqual(calc.raw_to_percent(1200, 2800, 1200), 100)

    def test_midpoint(self):
        self.assertEqual(calc.raw_to_percent(2000, 2800, 1200), 50)

    def test_clamps(self):
        self.assertEqual(calc.raw_to_percent(3500, 2800, 1200), 0)
        self.assertEqual(calc.raw_to_percent(500, 2800, 1200), 100)

    def test_equal_points_return_none(self):
        self.assertIsNone(calc.raw_to_percent(2000, 2000, 2000))


class SaltValidTest(unittest.TestCase):
    def test_inside_band(self):
        self.assertTrue(calc.salt_valid(60, 55, 75))
        self.assertTrue(calc.salt_valid(55, 55, 75))
        self.assertTrue(calc.salt_valid(75, 55, 75))

    def test_outside_band(self):
        self.assertFalse(calc.salt_valid(54, 55, 75))
        self.assertFalse(calc.salt_valid(76, 55, 75))

    def test_invalid_soil_reading(self):
        self.assertFalse(calc.salt_valid(None, 55, 75))


if __name__ == "__main__":
    unittest.main()
