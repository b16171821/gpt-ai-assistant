import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DispositionDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = json.loads(
            (ROOT / "data" / "disposition_risk.json").read_text(encoding="utf-8")
        )

    def test_all_current_tw_stocks_have_additive_risk_records(self):
        latest = json.loads((ROOT / "data" / "latest.json").read_text(encoding="utf-8-sig"))
        expected = {
            f"台股:{str(stock.get('code') or '').strip()}"
            for stock in latest.get("stocks", [])
            if str(stock.get("code") or "").strip()
        }
        actual = set(self.payload.get("records", {})) | {
            f"台股:{code}" for code in self.payload.get("normalCodes", [])
        }
        self.assertEqual(actual, expected)

    def test_statuses_and_counts_are_consistent(self):
        allowed = {"NORMAL", "WATCH", "WARNING", "DISPOSITION", "UNKNOWN"}
        actual_counts = {status: 0 for status in allowed}
        actual_counts["NORMAL"] = len(self.payload.get("normalCodes", []))
        for item in self.payload["records"].values():
            risk = item["dispositionRisk"]
            self.assertIn(risk["status"], allowed)
            actual_counts[risk["status"]] += 1
            self.assertTrue(risk.get("updatedAt") or self.payload["meta"].get("updatedAt"))
        expected_counts = self.payload["meta"]["counts"]
        self.assertEqual(actual_counts, {key: expected_counts[key] for key in allowed})

    def test_official_sources_and_new_rule_metadata_are_present(self):
        meta = self.payload["meta"]
        self.assertEqual(meta["ruleEffectiveDate"], "2026-08-10")
        self.assertEqual(meta["generalDispositionBusinessDays"], 5)
        self.assertEqual(meta["specialDispositionBusinessDays"], 7)
        urls = [url for source in meta["sources"] for url in source["urls"]]
        self.assertTrue(any("openapi.twse.com.tw" in url for url in urls))
        self.assertTrue(any("tpex.org.tw/openapi" in url for url in urls))


if __name__ == "__main__":
    unittest.main()
