import copy
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from scripts import check_update_freshness as freshness


def official(*days):
    return {"stat": "OK", "fields": ["日期", "收盤指數"],
            "data": [[day, "47,631.96"] for day in days]}


def stock_payload(day="2026-09-29"):
    return {"meta": {"officialDataDate": day, "strategyAsOfDate": day,
                     "freshnessStatus": "COMPLETE"},
            "stocks": [{"code": "2330", "date": day}]}


class UpdateFreshnessTests(unittest.TestCase):
    def test_current_payload_passes_without_mutation(self):
        payload = stock_payload()
        before = copy.deepcopy(payload)
        self.assertFalse(freshness.assess_payload(payload, "2026-09-29")["needsUpdate"])
        self.assertEqual(before, payload)

    def test_whole_batch_old_cannot_pass_by_internal_consistency(self):
        result = freshness.assess_payload(stock_payload("2026-09-24"), "2026-09-29")
        self.assertTrue(result["needsUpdate"])
        self.assertEqual(result["actualDate"], "2026-09-24")

    def test_metadata_cannot_hide_stale_row(self):
        payload = stock_payload()
        payload["stocks"][0]["date"] = "2026-09-24"
        self.assertTrue(freshness.assess_payload(payload, "2026-09-29")["needsUpdate"])

    def test_missing_empty_invalid_and_future_payloads_need_refresh(self):
        for payload in (None, {}, {"meta": None}, {"stocks": {}}, stock_payload("2026-09-30")):
            with self.subTest(payload=payload):
                self.assertTrue(freshness.assess_payload(payload, "2026-09-29")["needsUpdate"])

    def test_weekend_and_holidays_use_published_sessions(self):
        result = freshness.latest_completed_session(
            datetime(2026, 9, 28, 19, tzinfo=freshness.TW),
            lambda url: official("115/09/24") if "20260901" in url else official("115/08/31"))
        self.assertEqual(result, "2026-09-24")

    def test_first_day_of_month_falls_back_to_previous_month(self):
        result = freshness.latest_completed_session(
            datetime(2026, 10, 1, 8, tzinfo=freshness.TW),
            lambda url: official() if "20261001" in url else official("115/09/30"))
        self.assertEqual(result, "2026-09-30")

    def test_no_data_response_at_month_start(self):
        result = freshness.latest_completed_session(
            datetime(2026, 10, 1, 8, tzinfo=freshness.TW),
            lambda url: {"stat": "很抱歉，沒有符合條件的資料!"}
            if "20261001" in url else official("115/09/30"))
        self.assertEqual(result, "2026-09-30")

    def test_intraday_and_future_bars_ignored(self):
        result = freshness.latest_completed_session(
            datetime(2026, 9, 29, 12, tzinfo=freshness.TW),
            lambda url: official("115/09/24", "115/09/29", "115/09/30"))
        self.assertEqual(result, "2026-09-24")

    def test_after_close_includes_current_session(self):
        result = freshness.latest_completed_session(
            datetime(2026, 9, 29, 20, tzinfo=freshness.TW),
            lambda url: official("115/09/24", "115/09/29"))
        self.assertEqual(result, "2026-09-29")

    def test_upstream_failures_do_not_guess_date(self):
        with self.assertRaises(RuntimeError):
            freshness.latest_completed_session(requester=lambda url: (_ for _ in ()).throw(RuntimeError()))
        for payload in ([], {}, official("invalid"), {"stat": "OK", "fields": []},
                        {"stat": "OK", "fields": ["日期", "收盤指數"], "data": [["115/09/29", "NaN"]]}):
            with self.subTest(payload=payload), self.assertRaises((ValueError, TypeError)):
                freshness.session_dates(payload)

    def test_cli_gate_fails_but_watchdog_can_request_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "latest.json"
            output = Path(directory) / "output"
            path.write_text(json.dumps(stock_payload("2026-09-24")), encoding="utf-8")
            original = path.read_bytes()
            with patch.object(freshness, "latest_completed_session", return_value="2026-09-29"), \
                 patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}):
                with patch("sys.argv", ["check", "--path", str(path)]):
                    self.assertEqual(freshness.main(), 1)
                with patch("sys.argv", ["check", "--path", str(path), "--check-only"]):
                    self.assertEqual(freshness.main(), 0)
            self.assertIn("needs_update=true", output.read_text())
            self.assertEqual(original, path.read_bytes())


if __name__ == "__main__":
    unittest.main()
