import unittest
from tai_bootstrap_preflight import validate_bootstrap_reps

class BootstrapPreflightTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(validate_bootstrap_reps(100), 100)
        self.assertEqual(validate_bootstrap_reps(20000), 20000)
    def test_invalid(self):
        for value in (0, -1, 99, True, False, 1.5, "20000", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_bootstrap_reps(value)

if __name__ == "__main__":
    unittest.main()
