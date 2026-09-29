import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.update_site_visitors import export_report, parse_report, report_request


def report(value="123"):
    return {"metricHeaders": [{"name": "totalUsers", "type": "TYPE_INTEGER"}],
            "rows": [{"metricValues": [{"value": value}]}], "rowCount": 1}


class SiteVisitorTests(unittest.TestCase):
    def test_one_deduplicated_period_not_sum_of_days_or_pages(self):
        body = report_request("2026-09-30")
        self.assertEqual(body["metrics"], [{"name": "totalUsers"}])
        self.assertNotIn("dimensions", body)
        self.assertEqual(body["dateRanges"], [{"startDate": "2026-09-29", "endDate": "2026-09-30"}])
        filters = body["dimensionFilter"]["andGroup"]["expressions"]
        self.assertEqual(filters[0]["filter"]["stringFilter"]["value"], "b16171821.github.io")
        self.assertEqual(filters[1]["filter"]["stringFilter"]["value"], "/gpt-ai-assistant/")

    def test_valid_zero_and_count(self):
        self.assertEqual(parse_report(report()), 123)
        self.assertEqual(parse_report(report("0")), 0)
        empty = report()
        empty["rows"] = []
        empty["rowCount"] = 0
        self.assertEqual(parse_report(empty), 0)

    def test_reject_bad_counts_and_ambiguous_reports(self):
        for value in ["-1", "NaN", "1.5", "Infinity", "9007199254740992", None, 12]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_report(report(value))
        for value in [{}, {"error": "API unavailable"}, None]:
            with self.assertRaises(ValueError):
                parse_report(value)
        for metadata in [{"subjectToThresholding": True}, {"samplingMetadatas": [{}]}]:
            with self.assertRaises(ValueError):
                parse_report({**report(), "metadata": metadata})

    def test_only_public_aggregate_exported_and_api_failure_preserves_last_result(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "site_visitors.json"
            now = datetime(2026, 9, 29, 17, tzinfo=timezone.utc)
            result = export_report(lambda _: {**report(), "private": "not-for-public"}, path, now)
            self.assertEqual(result["throughDate"], "2026-09-30")
            self.assertEqual(result["totalUsers"], 123)
            old = path.read_text(encoding="utf-8")
            self.assertNotIn("not-for-public", old)
            self.assertEqual(json.loads(old), result)
            with self.assertRaises(ValueError):
                export_report(lambda _: {}, path, now)
            self.assertEqual(path.read_text(encoding="utf-8"), old)
            def failed(_):
                raise TimeoutError()
            with self.assertRaises(TimeoutError):
                export_report(failed, path, now)
            self.assertEqual(path.read_text(encoding="utf-8"), old)
