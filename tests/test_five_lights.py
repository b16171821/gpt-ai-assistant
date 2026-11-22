import json
import math
import subprocess
import sys
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import apo_five_lights as five  # noqa: E402
from tw_official_data import merge_official_bar  # noqa: E402


def linear_frame(periods=260, start=80.0, step=0.15, volume=1000, last_volume=None, last_close=None):
    dates = pd.bdate_range("2024-01-02", periods=periods)
    rows = []
    for index in range(periods):
        close = start + step * index
        rows.append(
            {
                "Open": close - 0.05,
                "High": close + 0.05,
                "Low": close - 0.08,
                "Close": close,
                "Volume": volume,
            }
        )
    if last_close is not None:
        rows[-1]["Close"] = last_close
        rows[-1]["Open"] = last_close - 0.05
        rows[-1]["High"] = last_close + 0.05
        rows[-1]["Low"] = last_close - 0.08
    if last_volume is not None:
        rows[-1]["Volume"] = last_volume
    return pd.DataFrame(rows, index=dates)


class FiveLightCoreTests(unittest.TestCase):
    def test_pullback_below_prior_high_is_not_chase(self):
        frame = linear_frame(periods=80, start=100, step=-0.4, volume=2000)
        row = five.analyze_ohlcv(frame, market="台股", symbol="T", name="下跌")
        self.assertIsNotNone(row)
        self.assertLess(row["distanceFromPrevHighPct"], 0)
        self.assertFalse(row["forbiddenChase"])
        self.assertFalse(row["extendedAbove"])
        self.assertIn(row["priceLocation"], {"跌破確認價", "區間內"})
        self.assertNotIn("禁止追高", row["status"])
        self.assertNotIn("漲幅過大", " ".join(row["exclusionReasons"]))

    def test_only_extension_above_prior_high_is_chase(self):
        frame = linear_frame(periods=80, start=50, step=0.2, volume=1000)
        # Jump the last close far above the prior 60-day high.
        last = float(frame.iloc[-2]["High"]) * 1.12
        frame.iloc[-1, frame.columns.get_loc("Close")] = last
        frame.iloc[-1, frame.columns.get_loc("High")] = last + 0.1
        frame.iloc[-1, frame.columns.get_loc("Open")] = last - 0.1
        frame.iloc[-1, frame.columns.get_loc("Volume")] = 5000
        row = five.analyze_ohlcv(frame, market="台股", symbol="T", name="延伸")
        self.assertTrue(row["extendedAbove"])
        self.assertTrue(row["forbiddenChase"])
        self.assertEqual(row["priceLocation"], "延伸在壓力之上")
        self.assertGreater(row["distanceFromNecklinePct"], 0)

    def test_volume_average_excludes_today(self):
        frame = linear_frame(periods=80, volume=100, last_volume=10000, step=0.2)
        row = five.analyze_ohlcv(frame, market="台股", symbol="T", name="量")
        self.assertEqual(row["avgVolume20"], 100)
        self.assertEqual(row["volumeRatio"], 100)

    def test_equal_directional_moves_zero_both_sides(self):
        high = pd.Series([10.0, 12.0, 14.0])
        low = pd.Series([8.0, 6.0, 5.0])
        close = pd.Series([9.0, 9.0, 10.0])
        plus, minus, _true_range = five.directional_moves(high, low, close)
        # Session 1: up move 2 equals down move 2. Both DM must be zero.
        self.assertEqual(plus.iloc[1], 0)
        self.assertEqual(minus.iloc[1], 0)
        # Session 2: up move 2 is smaller than down move 1, so only -DM remains? 
        # up = 14-12 = 2, down = 6-5 = 1. +DM wins.
        self.assertEqual(plus.iloc[2], 2)
        self.assertEqual(minus.iloc[2], 0)

    def test_wilder_smoothing_matches_hand_loop(self):
        values = pd.Series([1, 2, 3, 4, 5, 6, 7], dtype=float)
        smoothed = five.wilder_sum(values, 3)
        self.assertEqual(smoothed.iloc[2], 6)
        self.assertAlmostEqual(smoothed.iloc[3], 6 - 6 / 3 + 4)
        averaged = five.wilder_average(pd.Series([10, 20, 30, 40], dtype=float), 2)
        self.assertAlmostEqual(averaged.iloc[1], 15)
        self.assertAlmostEqual(averaged.iloc[2], (15 + 30) / 2)

    def test_wilder_adx_outputs_di_and_requires_plus_di(self):
        frame = linear_frame(periods=80, step=0.3)
        dmi = five.wilder_dmi(frame)
        self.assertGreater(dmi["ADX"].iloc[-1], 25)
        self.assertGreater(dmi["PLUS_DI"].iloc[-1], dmi["MINUS_DI"].iloc[-1])
        row = five.analyze_ohlcv(frame, market="台股", symbol="T", name="趨勢")
        self.assertIsNotNone(row["adx"])
        self.assertIsNotNone(row["plusDI"])
        self.assertIsNotNone(row["minusDI"])
        self.assertEqual(row["lights"][1]["state"], "通過")

    def test_official_history_drops_bars_after_the_official_date(self):
        frame = pd.DataFrame(
            {
                "Open": [10, 11, 12],
                "High": [11, 12, 13],
                "Low": [9, 10, 11],
                "Close": [10.5, 11.5, 12.5],
                "Adj Close": [10.5, 11.5, 12.5],
                "Volume": [100, 100, 900],
            },
            index=pd.to_datetime(["2026-07-28", "2026-07-29", "2026-07-30"]),
        )
        merged = merge_official_bar(
            frame,
            {"date": "2026-07-29", "open": 11, "high": 12, "low": 10, "close": 11.4, "volume": 150},
        )
        self.assertEqual(str(merged.index[-1].date()), "2026-07-29")
        self.assertNotIn(pd.Timestamp("2026-07-30"), merged.index)
        self.assertTrue(pd.isna(merged.iloc[-1]["Adj Close"]))
        self.assertEqual(merged.iloc[-1]["Close"], 11.4)

    def test_five_lights_pass_only_with_full_history_and_breakout(self):
        frame = linear_frame(periods=260, step=0.12, volume=1_000_000, last_volume=3_000_000)
        row = five.analyze_ohlcv(frame, market="台股", symbol="T", name="五燈", theme_text="AI", keywords=["AI"])
        self.assertEqual([light["state"] for light in row["lights"]], ["通過"] * 5)
        self.assertEqual(row["rawRating"], "待執行名單")
        self.assertEqual(row["lightsPassed"], 5)
        self.assertTrue(row["strictOk"])
        self.assertLess(row["stopLoss"], row["close"])
        self.assertGreaterEqual(row["riskRewardRatio"], 2)
        self.assertIn("題材", row["tags"])
        self.assertNotIn("題材", row["reason"])

    def test_missing_ma240_is_unverified_and_not_a_pass(self):
        frame = linear_frame(periods=120, step=0.2, volume=1_000_000, last_volume=4_000_000)
        row = five.analyze_ohlcv(frame, market="台股", symbol="T", name="短歷史")
        self.assertEqual(row["lights"][0]["state"], "未核實")
        self.assertFalse(row["lights"][0]["passed"])
        self.assertNotEqual(row["rawRating"], "待執行名單")
        self.assertEqual(row["lightsPassed"], 4)
        self.assertEqual(row["rawRating"], "高優先觀察")
        self.assertIn("未核實", row["status"])

    def test_price_outside_entry_zone_is_not_a_formal_signal(self):
        frame = linear_frame(periods=80, step=0, volume=1000)
        row = five.analyze_ohlcv(frame, market="台股", symbol="T", name="未突破")
        self.assertNotEqual(row["lights"][4]["state"], "通過")
        self.assertFalse(row["strictOk"])
        self.assertLessEqual(row["distanceFromNecklinePct"], 0)
        self.assertNotEqual(row["priceLocation"], "延伸在壓力之上")

    def test_position_size_uses_costs_lot_and_rejects_stop_above_price(self):
        sized = five.position_size(1_000_000, 100, 96, lot_size=1000, avg_turnover=10_000_000_000)
        per_share = five.round_trip_risk_per_share(100, 96)
        expected_raw = math.floor((1_000_000 * 0.01) / per_share)
        self.assertEqual(sized["sharesBeforeLot"], expected_raw)
        self.assertEqual(sized["shares"], expected_raw // 1000 * 1000)
        self.assertGreater(per_share, 4)
        blocked = five.position_size(1_000_000, 100, 101, lot_size=1000)
        self.assertEqual(blocked["shares"], 0)
        self.assertIn("停損", blocked["reason"])
        illiquid = five.position_size(1_000_000, 100, 96, lot_size=1, avg_turnover=1000, participation=0.05)
        self.assertTrue(illiquid["liquidityCapped"])
        self.assertLess(illiquid["shares"], expected_raw)

    def test_reward_risk_below_two_fails_and_costs_are_in_the_ratio(self):
        ratio, _risk, _reward = five.net_reward_risk(100, 90, 115)
        self.assertLess(ratio, 2)
        wider, _risk, _reward = five.net_reward_risk(100, 96, 115)
        self.assertGreaterEqual(wider, 2)
        no_cost, _risk, _reward = five.net_reward_risk(100, 96, 115, fee_rate=0, fee_discount=0, sell_tax_rate=0)
        self.assertGreater(no_cost, wider)

    def test_history_appends_and_dedupes_same_day(self):
        path = ROOT / "tests" / "fixtures" / "_history_tmp.csv"
        if path.exists():
            path.unlink()
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        first = {"date": "2026-11-20", "code": "2330", "name": "甲", "rating": "觀察", "close": 100}
        second = {"date": "2026-11-21", "code": "2330", "name": "甲", "rating": "不符", "close": 101}
        five.append_history(path, [first])
        five.append_history(path, [second, {**first, "close": 99, "rating": "高優先觀察"}])
        text = path.read_text(encoding="utf-8-sig")
        self.assertEqual(text.count("2330"), 2)
        self.assertIn("高優先觀察", text)
        self.assertNotIn(",觀察,", text)

    def test_frontend_position_formula_matches_python(self):
        script = ROOT / "apo-five-lights.js"
        cases = [
            {"capital": 500000, "entry": 100, "stop": 95, "lotSize": 1, "avgTurnover": 0},
            {"capital": 1000000, "entry": 89.5, "stop": 85.95, "lotSize": 1000, "avgTurnover": 80000000},
            {"capital": 10000, "entry": 100, "stop": 95, "lotSize": 1, "avgTurnover": 0},
        ]
        node = subprocess.run(
            [
                "node",
                "-e",
                (
                    "const fs=require('fs');const vm=require('vm');"
                    "const ctx={console};"
                    "vm.createContext(ctx);"
                    "vm.runInContext(fs.readFileSync(process.argv[1],'utf8'), ctx);"
                    "const cases=JSON.parse(process.argv[2]);"
                    "const out=cases.map(c=>ctx.ApoFiveLights.positionSize(c));"
                    "console.log(JSON.stringify(out));"
                ),
                str(script),
                json.dumps(cases),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        js_rows = json.loads(node.stdout)
        for case, js_row in zip(cases, js_rows):
            py_row = five.position_size(
                case["capital"],
                case["entry"],
                case["stop"],
                lot_size=case["lotSize"],
                avg_turnover=case["avgTurnover"] or None,
            )
            self.assertEqual(js_row["shares"], py_row["shares"])
            self.assertAlmostEqual(js_row["riskPerShare"], py_row["riskPerShare"], places=8)


if __name__ == "__main__":
    unittest.main()
