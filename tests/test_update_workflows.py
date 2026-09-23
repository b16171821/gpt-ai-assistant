"""Prevent competing workflows from rebasing generated market snapshots."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ("update-data.yml", "update-four-lights.yml", "update-us-four-lights.yml")


class UpdateWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.sources = {
            name: (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
            for name in WORKFLOWS
        }

    def test_all_writers_share_workflow_lock(self):
        for name, source in self.sources.items():
            with self.subTest(workflow=name):
                self.assertRegex(source, r"(?m)^concurrency:\n  group: stock-data-main\n  cancel-in-progress: false")
                self.assertEqual(len(re.findall(r"(?m)^\s*concurrency:", source)), 1)

    def test_checkout_and_dispatch_are_main_only(self):
        for name, source in self.sources.items():
            with self.subTest(workflow=name):
                self.assertIn("if: github.ref == 'refs/heads/main'", source)
                self.assertRegex(source, r"with:\n\s+ref: main\n\s+fetch-depth: 0")

    def test_sync_happens_before_scanning(self):
        for name, source in self.sources.items():
            with self.subTest(workflow=name):
                sync = source.index("git pull --ff-only origin main")
                scan = re.search(r"python scripts/update_(tw|us)_stock_scanner\.py", source)
                self.assertIsNotNone(scan)
                self.assertLess(sync, scan.start())

    def test_generated_data_is_not_rebased_or_force_pushed(self):
        for name, source in self.sources.items():
            with self.subTest(workflow=name):
                self.assertNotIn("--rebase", source)
                self.assertNotIn("--autostash", source)
                self.assertNotRegex(source, r"git (?:push.*(?:--force|-f\b)|reset --hard)")
                self.assertIn("git push", source)

    def test_earlier_schedule_is_preserved(self):
        source = self.sources["update-data.yml"]
        for cron in ("40 6 * * 1-5", "40 7 * * 1-5", "40 22 * * 1-5"):
            self.assertIn(f"cron: '{cron}'", source)


if __name__ == "__main__":
    unittest.main()
