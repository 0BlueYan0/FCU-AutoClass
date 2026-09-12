"""Tests for the optional config keys added for the HTTP engine."""
import unittest

import utilities as utils


class ConfigKeyTests(unittest.TestCase):
    def test_engine_defaults_and_validation(self):
        self.assertEqual(utils.get_engine(None), "http")
        self.assertEqual(utils.get_engine(""), "http")
        self.assertEqual(utils.get_engine(" HTTP "), "http")
        self.assertEqual(utils.get_engine("selenium"), "selenium")
        with self.assertLogs(level="WARNING"):
            self.assertEqual(utils.get_engine("firefox"), "http")

    def test_query_interval_defaults_and_clamping(self):
        self.assertEqual(utils.get_query_interval(None), utils.DEFAULT_QUERY_INTERVAL)
        self.assertEqual(utils.get_query_interval(""), utils.DEFAULT_QUERY_INTERVAL)
        self.assertEqual(utils.get_query_interval("1.5"), 1.5)
        self.assertEqual(utils.get_query_interval(2), 2.0)
        with self.assertLogs(level="WARNING"):
            self.assertEqual(utils.get_query_interval(0.1), utils.MIN_QUERY_INTERVAL)
        with self.assertLogs(level="WARNING"):
            self.assertEqual(utils.get_query_interval(0), utils.MIN_QUERY_INTERVAL)
        with self.assertLogs(level="WARNING"):
            self.assertEqual(utils.get_query_interval("fast"), utils.DEFAULT_QUERY_INTERVAL)


if __name__ == "__main__":
    unittest.main()
