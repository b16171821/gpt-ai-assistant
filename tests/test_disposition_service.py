import sys
import unittest
from datetime import date
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from disposition_service import (  # noqa: E402
    calculate_disposition_risk,
    get_disposition_end_date,
    parse_disposition_period,
    remaining_trading_days,
)


class DispositionServiceTests(unittest.TestCase):
    def risk(self, **kwargs):
        return calculate_disposition_risk(
            as_of=kwargs.pop("as_of", date(2026, 8, 10)),
            updated_at="2026-08-10T18:30:00+08:00",
            source_name="官方測試資料",
            source_url="https://example.invalid/official",
            **kwargs,
        )

    def test_normal_stock(self):
        result = self.risk()
        self.assertEqual(result["status"], "NORMAL")
        self.assertTrue(result["eligibleForNormalEntry"])

    def test_attention_stock(self):
        result = self.risk(
            watch_row={
                "Date": "1150810",
                "NumberOfAnnouncement": "2",
                "TradingInformation": "最近六個營業日累積收盤價漲幅異常",
            }
        )
        self.assertEqual(result["status"], "WATCH")
        self.assertEqual(result["watchCount"], 2)

    def test_official_near_disposition_warning(self):
        result = self.risk(
            warning_row={
                "Date": "1150810",
                "AccumulationSituation": "最近十個營業日內已有五次公布注意",
            }
        )
        self.assertEqual(result["status"], "WARNING")
        self.assertEqual(result["watchCount"], 5)
        self.assertEqual(result["remainingToDisposition"], 1)

    def test_chinese_watch_count_from_official_warning(self):
        result = self.risk(
            warning_row={
                "Date": "20260807",
                "AccumulationSituation": "115年08月06日至115年08月07日連續二次",
            }
        )
        self.assertEqual(result["watchCount"], 2)

    def test_general_five_business_days(self):
        end = get_disposition_end_date(date(2026, 8, 10), False)
        self.assertEqual(end, date(2026, 8, 14))
        result = self.risk(
            disposition_row={
                "DispositionPeriod": "115/08/10～115/08/14",
                "Detail": "處置期間五個營業日，約每二分鐘撮合一次。",
            }
        )
        self.assertEqual(result["remainingTradingDays"], 5)
        self.assertEqual(result["periodBusinessDays"], 5)
        self.assertEqual(result["matchInterval"], "約每 2 分鐘")

    def test_special_seven_business_days(self):
        end = get_disposition_end_date(date(2026, 8, 10), True)
        self.assertEqual(end, date(2026, 8, 18))
        result = self.risk(
            disposition_row={
                "DispositionPeriod": "115/08/10～115/08/18",
                "Detail": "處置期間七個營業日，約每二分鐘撮合一次。",
            }
        )
        self.assertEqual(result["remainingTradingDays"], 7)
        self.assertEqual(result["periodBusinessDays"], 7)

    def test_weekend_and_exchange_holiday_are_excluded(self):
        holiday = date(2026, 8, 12)
        end = get_disposition_end_date(date(2026, 8, 10), False, {holiday})
        self.assertEqual(end, date(2026, 8, 17))
        self.assertEqual(
            remaining_trading_days(date(2026, 8, 10), date(2026, 8, 10), end, {holiday}),
            5,
        )

    def test_status_returns_to_normal_after_official_end(self):
        result = self.risk(
            as_of=date(2026, 8, 17),
            disposition_row={
                "DispositionPeriod": "115/08/10～115/08/14",
                "Detail": "處置期間五個營業日。",
            },
        )
        self.assertEqual(result["status"], "NORMAL")

    def test_official_adjusted_end_overrides_old_period(self):
        start, end = parse_disposition_period(
            {
                "DispositionPeriod": "115/08/07～115/08/20",
                "Detail": "修正其處置至一百十五年八月十三日止，改以約每二分鐘撮合一次。",
            }
        )
        self.assertEqual(start, date(2026, 8, 7))
        self.assertEqual(end, date(2026, 8, 13))

    def test_api_failure_is_unknown_not_guessed(self):
        result = self.risk(source_available=False)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertFalse(result["dataAvailable"])
        self.assertFalse(result["eligibleForNormalEntry"])


if __name__ == "__main__":
    unittest.main()
