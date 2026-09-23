import sys
import unittest
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from update_tracking_context import quote_symbol, return_window, stock_evidence


class TrackingContextTests(unittest.TestCase):
    def test_exchange_comes_from_scanner_not_a_guessed_suffix(self):
        self.assertEqual(quote_symbol({"code": "2330", "market": "上市"}), "2330.TW")
        self.assertEqual(quote_symbol({"code": "3441", "market": "上櫃"}), "3441.TWO")
        self.assertIsNone(quote_symbol({"code": "1234", "market": "未知"}))

    def frame(self):
        return pd.DataFrame({"Close": [95, 96, 97, 98, 99, 100]},
                            index=pd.to_datetime(["2026-09-15", "2026-09-16", "2026-09-17",
                                                  "2026-09-18", "2026-09-21", "2026-09-22"]))

    def test_five_sessions_not_five_calendar_days(self):
        dates, value = return_window(self.frame(), "2026-09-22")
        self.assertEqual(len(dates), 6)
        self.assertAlmostEqual(value, (100 / 95 - 1) * 100)

    def test_missing_bar_or_mismatched_period_rejected(self):
        with self.assertRaises(ValueError):
            stock_evidence(self.frame().iloc[1:], self.frame(), "2026-09-22",
                           {"close": 100, "turnover": 60_000_000})

    def test_future_bars_are_excluded(self):
        frame = pd.concat([self.frame(), pd.DataFrame({"Close": [999]}, index=pd.to_datetime(["2026-09-23"]))])
        self.assertEqual(return_window(frame, "2026-09-22"), return_window(self.frame(), "2026-09-22"))

    def test_disagreeing_close_is_unavailable_not_guessed(self):
        with self.assertRaises(ValueError):
            stock_evidence(self.frame(), self.frame(), "2026-09-22", {"close": 98, "turnover": 60_000_000})

    def test_valid_evidence_uses_existing_liquidity_threshold(self):
        result = stock_evidence(self.frame(), self.frame(), "2026-09-22", {"close": 100, "turnover": 60_000_000})
        self.assertTrue(result["liquidityOk"])
        self.assertEqual(result["status"], "COMPLETE")
