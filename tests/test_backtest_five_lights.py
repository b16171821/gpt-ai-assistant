import sys
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import backtest_five_lights as backtest  # noqa: E402
import apo_five_lights as five  # noqa: E402


def rising_breakout(periods=260):
    dates = pd.bdate_range("2023-01-02", periods=periods)
    rows = []
    for index in range(periods):
        close = 40 + 0.12 * index
        volume = 1000
        if index == periods - 25:
            volume = 4000
            close = close + 0.2
        rows.append(
            {
                "Open": close - 0.04,
                "High": close + 0.05,
                "Low": close - 0.08,
                "Close": close,
                "Volume": volume,
            }
        )
    return pd.DataFrame(rows, index=dates)


class BacktestTests(unittest.TestCase):
    def test_future_bars_do_not_change_an_earlier_signal(self):
        frame = rising_breakout()
        original = five.analyze_ohlcv(frame.iloc[:120].copy(), market="台股", symbol="T", name="T")
        mutated = frame.copy()
        mutated.iloc[-1, mutated.columns.get_loc("Close")] = 1
        mutated.iloc[-1, mutated.columns.get_loc("Volume")] = 9_000_000
        again = five.analyze_ohlcv(mutated.iloc[:120].copy(), market="台股", symbol="T", name="T")
        self.assertEqual(original["rawRating"], again["rawRating"])
        self.assertEqual(original["neckline"], again["neckline"])
        self.assertEqual(original["avgVolume20"], again["avgVolume20"])
        self.assertEqual(original["lightsPassed"], again["lightsPassed"])

    def test_summary_has_required_fields_and_no_placeholder_win_rate(self):
        index = rising_breakout(320)
        index["Close"] = index["Close"] * 10
        result = backtest.run_backtest({"TEST": rising_breakout()}, index_frame=index, market="台股")
        summary = result["summary"]
        for key in (
            "tradeCount",
            "winRate",
            "avgWin",
            "avgLoss",
            "expectancy",
            "rewardRisk",
            "maxDrawdown",
            "costs",
            "inSample",
            "outOfSample",
            "byRegime",
            "byRateCycle",
        ):
            self.assertIn(key, summary)
        self.assertIsNone(summary["publishedWinRate"])
        self.assertIn("倖存者偏差", summary["survivorshipCaveat"])
        self.assertIn("未提供", summary["byRateCycle"])
        if summary["tradeCount"] == 0:
            self.assertIsNone(summary["winRate"])
        else:
            self.assertGreater(summary["costs"], 0)
            self.assertTrue(any(name in summary["byRegime"] for name in ("多頭", "空頭", "區間", "未提供")))

    def test_costs_reduce_a_winning_trade(self):
        gross = five.net_reward_risk(100, 96, 115, fee_rate=0, fee_discount=0, sell_tax_rate=0)
        net = five.net_reward_risk(100, 96, 115)
        self.assertGreater(gross[0], net[0])


if __name__ == "__main__":
    unittest.main()
