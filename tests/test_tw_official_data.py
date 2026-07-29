import sys
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from tw_official_data import (  # noqa: E402
    OfficialDataError,
    merge_official_bar,
    normalize_tw_date,
    validate_official_coverage,
)


class TaiwanOfficialDataTests(unittest.TestCase):
    def test_normalizes_roc_and_gregorian_dates(self):
        self.assertEqual(normalize_tw_date("1150729"), "2026-07-29")
        self.assertEqual(normalize_tw_date("115/07/29"), "2026-07-29")
        self.assertEqual(normalize_tw_date("2026/07/29"), "2026-07-29")

    def test_official_bar_replaces_same_day_and_appends_new_day(self):
        frame = pd.DataFrame(
            {
                "Open": [20.0, 21.0],
                "High": [21.0, 22.0],
                "Low": [19.0, 20.0],
                "Close": [20.5, 21.5],
                "Volume": [1000, 2000],
            },
            index=pd.to_datetime(["2026-07-27", "2026-07-28"]),
        )
        quote = {
            "date": "2026-07-29",
            "open": 24.8,
            "high": 24.8,
            "low": 24.5,
            "close": 24.5,
            "volume": 106460,
        }
        merged = merge_official_bar(frame, quote)
        self.assertEqual(str(merged.index[-1].date()), "2026-07-29")
        self.assertEqual(merged.iloc[-1]["Close"], 24.5)
        self.assertEqual(merged.iloc[-1]["Volume"], 106460)

        quote["close"] = 25.0
        replaced = merge_official_bar(merged, quote)
        self.assertEqual(len(replaced), 3)
        self.assertEqual(replaced.iloc[-1]["Close"], 25.0)

    def test_coverage_requires_nearly_all_scanner_codes(self):
        result = validate_official_coverage(["1101", "1102", "3288"], {"1101": {}, "1102": {}, "3288": {}})
        self.assertEqual(result["coveragePct"], 100)
        with self.assertRaises(OfficialDataError):
            validate_official_coverage(["1101", "1102", "3288"], {"1101": {}}, minimum_pct=95)


if __name__ == "__main__":
    unittest.main()
