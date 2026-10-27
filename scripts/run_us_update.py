"""Retry the unchanged US scanner; restore last-good files on any failure."""

import json
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = ("us_latest.json", "us_latest.csv", "us_latest_report.md")


def validated_date(path):
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    meta = payload.get("meta", {})
    session = meta.get("strategyAsOfDate")
    date.fromisoformat(session)
    rows = payload.get("stocks")
    if meta.get("freshnessStatus") != "COMPLETE" or not rows:
        raise ValueError("US scanner did not produce complete closing data")
    if not isinstance(rows, list) or any(
        not isinstance(row, dict) or row.get("strategyAsOfDate") != session
        for row in rows
    ):
        raise ValueError("US closing dates are inconsistent")
    return session


def run_update(root=ROOT, attempts=3, runner=subprocess.run, sleep=time.sleep):
    paths = [root / "data" / name for name in OUTPUTS]
    original = {path: path.read_bytes() if path.exists() else None for path in paths}
    try:
        previous_date = validated_date(paths[0])
    except (OSError, ValueError, TypeError, AttributeError):
        previous_date = None

    def restore():
        for path, content in original.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(content)

    for attempt in range(attempts):
        try:
            result = runner(
                [sys.executable, "scripts/update_us_stock_scanner.py"],
                cwd=root, timeout=300, check=False,
            )
            if result.returncode:
                raise ValueError("US scanner exited unsuccessfully")
            current_date = validated_date(paths[0])
            if previous_date and current_date < previous_date:
                raise ValueError("US scanner returned an older closing date")
            print(f"US update verified: {current_date}")
            return 0
        except (OSError, ValueError, TypeError, AttributeError, subprocess.TimeoutExpired) as exc:
            restore()
            print(f"US update attempt {attempt + 1}/{attempts} failed: {type(exc).__name__}", flush=True)
            if attempt + 1 < attempts:
                sleep(20 * (attempt + 1))
    print("::error title=US update unavailable::Last-good US files restored; their original data dates are unchanged.")
    return 1


if __name__ == "__main__":
    raise SystemExit(run_update())
