import math
import unittest
from datetime import date
from pathlib import Path

from scripts.check_data_health import (
    ROOT,
    build_health_report,
    find_non_finite,
    validate_ohlcv_series,
    validate_snapshots,
    validate_stock_payload,
)


def valid_payload():
    return {
        "meta": {
            "updatedAt": "2026-08-10T06:30:00+08:00",
            "strategyAsOfDate": "2026-08-07",
            "officialDataDate": "2026-08-07",
            "freshnessStatus": "COMPLETE",
            "staleCount": 0,
            "totalAnalyzed": 1,
            "source": "fixture",
        },
        "stocks": [
            {
                "code": "2330",
                "name": "台積電",
                "date": "2026-08-07",
                "strategyAsOfDate": "2026-08-07",
                "open": 100.0,
                "high": 105.0,
                "low": 99.0,
                "close": 103.0,
                "volume": 1000,
                "dataFresh": True,
            }
        ],
    }


class DataHealthTests(unittest.TestCase):
    def test_valid_stock_payload_is_healthy(self):
        result = validate_stock_payload(valid_payload(), "台股")
        self.assertEqual(result["status"], "HEALTHY")
        self.assertEqual(result["completenessPct"], 100)

    def test_duplicate_invalid_ohlcv_and_stale_date_fail(self):
        payload = valid_payload()
        payload["meta"]["totalAnalyzed"] = 2
        bad = dict(payload["stocks"][0])
        bad.update({"high": 90.0, "low": 110.0, "close": 120.0, "volume": -1, "date": "2026-08-06", "strategyAsOfDate": "2026-08-06"})
        payload["stocks"].append(bad)
        result = validate_stock_payload(payload, "台股")
        codes = {item["code"] for item in result["issues"]}
        self.assertEqual(result["status"], "ERROR")
        self.assertTrue({"DUPLICATE_SYMBOL", "HIGH_BELOW_LOW", "INVALID_VOLUME", "STALE_ROW_DATE"}.issubset(codes))

    def test_non_finite_values_are_reported(self):
        paths = find_non_finite({"a": [1, math.nan, math.inf]})
        self.assertEqual(paths, ["$.a[1]", "$.a[2]"])

    def test_snapshot_duplicate_and_limit_fail(self):
        rows = [{"date": "2026-08-07"}] * 6
        result = validate_snapshots({"台股:2330": rows})
        codes = {item["code"] for item in result["issues"]}
        self.assertIn("TOO_MANY_SNAPSHOTS", codes)
        self.assertIn("DUPLICATE_SNAPSHOT_DATE", codes)

    def test_missing_trading_day_excludes_weekend_and_declared_holiday(self):
        rows = [
            {"date": "2026-08-07"},
            {"date": "2026-08-11"},
            {"date": "2026-08-12"},
        ]
        missing = validate_ohlcv_series(rows)
        self.assertIn("MISSING_TRADING_DAY", {item["code"] for item in missing["issues"]})
        healthy = validate_ohlcv_series(rows, {date(2026, 8, 10)})
        self.assertEqual(healthy["status"], "HEALTHY")

    def test_api_timeout_and_rate_limit_are_explicit(self):
        payload = valid_payload()
        payload["meta"]["errors"] = ["request timed out", "HTTP 429 rate limit"]
        result = validate_stock_payload(payload, "台股")
        codes = {item["code"] for item in result["issues"]}
        self.assertTrue({"API_TIMEOUT", "API_RATE_LIMIT", "UPSTREAM_ERRORS"}.issubset(codes))
        self.assertEqual(result["apiStatus"], "DEGRADED")

    def test_current_repository_data_has_no_integrity_error(self):
        result = build_health_report(Path(ROOT))
        self.assertEqual(result["meta"]["errorCount"], 0, result)


if __name__ == "__main__":
    unittest.main()
