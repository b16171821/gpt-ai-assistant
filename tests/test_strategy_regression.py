import copy
import json
import math
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "strategy-sample.json"
sys.path.insert(0, str(ROOT / "scripts"))

import update_tw_stock_scanner as scanner  # noqa: E402


BASELINE_FIELDS = (
    "code",
    "name",
    "grade",
    "safeScore",
    "score",
    "strictOk",
    "stage",
    "category",
    "status",
    "neckline",
    "observationEntry",
    "stopLoss",
    "target",
    "riskPct",
    "rewardPct",
    "riskRewardRatio",
    "distanceFromNecklinePct",
    "action",
    "reason",
)


def build_frame(spec):
    periods = int(spec["periods"])
    dates = pd.bdate_range(spec["startDate"], periods=periods)
    rows = []
    overrides = spec.get("overrides", {})
    for index in range(periods):
        close = float(spec["closeStart"]) + float(spec["closeStep"]) * index
        row = {
            "Open": close + float(spec["openOffset"]),
            "High": close + float(spec["highOffset"]),
            "Low": close + float(spec["lowOffset"]),
            "Close": close,
            "Volume": int(spec["volume"]),
        }
        row.update(overrides.get(str(index), {}))
        rows.append(row)
    return pd.DataFrame(rows, index=dates)


def project_row(row):
    projected = {}
    for field in BASELINE_FIELDS:
        value = row.get(field)
        if isinstance(value, float) and not math.isfinite(value):
            value = None
        projected[field] = value
    return projected


def evaluate_fixture(payload):
    cases = payload["cases"]
    frames = {case["meta"]["ticker"]: build_frame(case["series"]) for case in cases}
    master = [copy.deepcopy(case["meta"]) for case in cases]
    official_date = str(next(iter(frames.values())).index[-1].date())
    quotes = {
        case["meta"]["code"]: {"date": official_date}
        for case in cases
    }

    def fake_download(ticker, **_kwargs):
        return frames[ticker].copy(deep=True)

    attack_filter = {
        "marketRegime": "ATTACK",
        "regimeLabel": "進攻盤",
        "strategy": "baseline",
    }
    coverage = {
        "coveragePct": 100,
        "coveredCount": len(cases),
        "expectedCount": len(cases),
    }
    with (
        patch.object(scanner, "BATCH_SIZE", 1),
        patch.object(scanner, "get_stock_master", return_value=master),
        patch.object(scanner, "load_theme_config", return_value=payload["themeConfig"]),
        patch.object(
            scanner,
            "load_latest_official_quotes",
            return_value={
                "officialDate": official_date,
                "quotes": quotes,
                "officialQuoteCount": len(cases),
                "listedCount": 2,
                "otcCount": 1,
                "source": "fixture",
            },
        ),
        patch.object(scanner, "validate_official_coverage", return_value=coverage),
        patch.object(scanner, "build_market_filter", return_value=attack_filter),
        patch.object(scanner, "merge_official_bar", side_effect=lambda frame, _quote: frame),
        patch.object(scanner, "previous_a_count_from_snapshots", return_value=0),
        patch.object(scanner, "apply_breadth_guard", side_effect=lambda market, *_args: market),
        patch.object(scanner, "apply_market_filter_to_rows", side_effect=lambda rows, _market: rows),
        patch.object(scanner.yf, "download", side_effect=fake_download),
    ):
        rows, errors, total, market_filter, freshness = scanner.scan_market()

    if errors:
        raise AssertionError(f"Unexpected fixture scan errors: {errors}")
    if total != len(cases) or market_filter["marketRegime"] != "ATTACK":
        raise AssertionError("Fixture harness did not exercise the expected scanner path")
    if freshness["officialDataDate"] != official_date:
        raise AssertionError("Fixture freshness date mismatch")
    return [project_row(row) for row in rows]


class StrategyRegressionTests(unittest.TestCase):
    def test_frozen_tw_strategy_output_and_order(self):
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        actual = evaluate_fixture(payload)
        self.assertEqual(
            actual,
            payload["expected"],
            "Protected strategy output changed. Review Expected/Actual diff; do not update the baseline automatically.",
        )


if __name__ == "__main__":
    unittest.main()
