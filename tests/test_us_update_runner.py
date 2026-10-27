import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from scripts.run_us_update import OUTPUTS, run_update


class USUpdateRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "data").mkdir()
        self.write_snapshot("2026-10-23")
        self.original = {name: self.path(name).read_bytes() for name in OUTPUTS}
        self.path("latest.json").write_text("unchanged Taiwan data")

    def path(self, name):
        return self.root / "data" / name

    def write_snapshot(self, session, stocks=None):
        payload = {"meta": {"strategyAsOfDate": session, "freshnessStatus": "COMPLETE"},
                   "stocks": stocks if stocks is not None else [{"strategyAsOfDate": session}]}
        self.path(OUTPUTS[0]).write_text(json.dumps(payload))
        for name in OUTPUTS[1:]:
            self.path(name).write_text(session)

    def run_fake(self, runner, attempts=3):
        return run_update(self.root, attempts, runner, lambda _: None)

    def assert_restored(self):
        for name, content in self.original.items():
            self.assertEqual(self.path(name).read_bytes(), content)
        self.assertEqual(self.path("latest.json").read_text(), "unchanged Taiwan data")

    def test_success_keeps_new_data(self):
        def runner(*args, **kwargs):
            self.write_snapshot("2026-10-26")
            return SimpleNamespace(returncode=0)
        self.assertEqual(self.run_fake(runner), 0)
        self.assertIn("2026-10-26", self.path(OUTPUTS[0]).read_text())

    def test_partial_failure_restores_all_files_before_retry(self):
        calls = []
        def runner(*args, **kwargs):
            self.assert_restored()
            calls.append(1)
            self.write_snapshot("2026-10-26")
            return SimpleNamespace(returncode=1 if len(calls) == 1 else 0)
        self.assertEqual(self.run_fake(runner), 0)
        self.assertEqual(len(calls), 2)

    def test_exhausted_retries_restore_old_dates_and_files(self):
        def runner(*args, **kwargs):
            self.write_snapshot("2026-10-26", [])
            return SimpleNamespace(returncode=1)
        self.assertEqual(self.run_fake(runner), 1)
        self.assert_restored()

    def test_invalid_success_and_date_regression_are_rejected(self):
        for session, rows in [("2026-10-22", None), ("2026-10-26", []),
                              ("2026-10-26", [{"strategyAsOfDate": "2026-10-23"}])]:
            with self.subTest(session=session, rows=rows):
                def runner(*args, **kwargs):
                    self.write_snapshot(session, rows)
                    return SimpleNamespace(returncode=0)
                self.assertEqual(self.run_fake(runner, 1), 1)
                self.assert_restored()

    def test_timeout_restores_last_good(self):
        def runner(*args, **kwargs):
            self.write_snapshot("2026-10-26")
            raise subprocess.TimeoutExpired("scanner", 300)
        self.assertEqual(self.run_fake(runner, 1), 1)
        self.assert_restored()

    def test_missing_baseline_does_not_publish_failed_partial_files(self):
        for name in OUTPUTS:
            self.path(name).unlink()
        def runner(*args, **kwargs):
            self.write_snapshot("2026-10-26")
            return SimpleNamespace(returncode=1)
        self.assertEqual(self.run_fake(runner, 1), 1)
        self.assertTrue(all(not self.path(name).exists() for name in OUTPUTS))


if __name__ == "__main__":
    unittest.main()
