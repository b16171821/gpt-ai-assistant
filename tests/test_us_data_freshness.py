import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from scripts import update_us_stock_scanner as scanner


class UsDataFreshnessTests(unittest.TestCase):
    def test_intraday_bar_is_removed_but_previous_close_is_kept(self):
        frame = pd.DataFrame(
            {"Close": [100.0, 101.0, 102.0]},
            index=pd.to_datetime(["2026-07-27", "2026-07-28", "2026-07-29"]),
        )
        current = datetime(2026, 7, 29, 14, 0, tzinfo=ZoneInfo("America/New_York"))

        with patch.object(scanner, "now_et", return_value=current):
            result = scanner.trim_to_completed_daily_bars(frame)

        self.assertEqual(str(result.index[-1].date()), "2026-07-28")

    def test_download_uses_explicit_end_after_current_day(self):
        current = datetime(2026, 7, 29, 14, 0, tzinfo=ZoneInfo("America/New_York"))

        with patch.object(scanner, "now_et", return_value=current), patch.object(
            scanner.yf, "download", return_value=pd.DataFrame()
        ) as download:
            scanner.download_group(["AAPL", "MSFT"])

        kwargs = download.call_args.kwargs
        self.assertEqual(kwargs["end"], "2026-07-30")
        self.assertNotIn("period", kwargs)

    def test_market_date_requires_two_indexes_to_agree(self):
        frame = pd.DataFrame(
            {"Close": [100.0, 101.0]},
            index=pd.to_datetime(["2026-07-27", "2026-07-28"]),
        )
        current = datetime(2026, 7, 29, 14, 0, tzinfo=ZoneInfo("America/New_York"))

        with patch.object(scanner, "now_et", return_value=current):
            result = scanner.latest_completed_market_date(
                {"SPX": frame, "NASDAQ": frame, "DJI": pd.DataFrame()}
            )

        self.assertEqual(result, "2026-07-28")


if __name__ == "__main__":
    unittest.main()
