import sys
import unittest
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from update_strategy_tracking import (  # noqa: E402
    ACTIVE_STATUSES,
    create_tracking_record,
    process_tracking,
    update_tracking_status,
)


def base_row(**overrides):
    row = {
        "date": "2026-07-13",
        "strategyAsOfDate": "2026-07-13",
        "code": "3374",
        "name": "精材",
        "score": 4,
        "grade": "A",
        "safeScore": 88,
        "riskRewardRatio": 2.5,
        "riskPct": 4.5,
        "distanceFromNecklinePct": -1,
        "close": 99,
        "high": 100,
        "low": 98,
        "volume": 1000000,
        "ma5": 98,
        "ma10": 97,
        "ma20": 95,
        "neckline": 100,
        "observationEntry": 102,
        "chaseRangeLow": 101,
        "chaseRangeHigh": 105,
        "stopLoss": 95,
        "target": 130,
        "stage": "接近成形",
        "originalSignal": "突破買點",
        "adjustedSignal": "等待確認",
        "strictOk": True,
        "forbiddenChase": False,
    }
    row.update(overrides)
    return row


def payload(row=None, regime="ATTACK", date="2026-07-13"):
    return {
        "meta": {
            "strategyAsOfDate": date,
            "marketRegime": regime,
            "marketRegimeLabel": {
                "ATTACK": "進攻盤",
                "WATCH": "觀察盤",
                "DEFENSE": "防守盤",
                "STOP": "停止進場",
            }.get(regime, "資料不足"),
            "marketFilter": {
                "marketRegime": regime,
                "regimeLabel": regime,
            },
        },
        "stocks": [row] if row else [],
    }


class StrategyTrackingTests(unittest.TestCase):
    def test_not_selected_next_day_remains_active(self):
        first = process_tracking(
            {"meta": {}, "records": []},
            payload(base_row()),
            payload(None),
        )
        second = process_tracking(
            first,
            payload(None, date="2026-07-14"),
            payload(None),
        )

        self.assertEqual(len(second["records"]), 1)
        self.assertIn(second["records"][0]["trackingStatus"], ACTIVE_STATUSES)

    def test_breaking_original_stop_moves_to_failed(self):
        record = create_tracking_record("台股", base_row())
        failed = update_tracking_status(
            record,
            base_row(
                strategyAsOfDate="2026-07-14",
                date="2026-07-14",
                close=94,
                high=96,
                low=93,
            ),
            "ATTACK",
            "2026-07-14",
        )

        self.assertEqual(failed["trackingStatus"], "FAILED")
        self.assertEqual(failed["result"], "型態失敗")
        self.assertEqual(failed["endDate"], "2026-07-14")

    def test_new_signal_does_not_overwrite_original_strategy(self):
        record = create_tracking_record("台股", base_row())
        original = deepcopy(record["originalStrategy"])
        next_row = base_row(
            strategyAsOfDate="2026-07-14",
            date="2026-07-14",
            neckline=110,
            stopLoss=104,
            target=150,
            adjustedSignal="今日優先觀察",
        )
        updated = process_tracking(
            {"meta": {}, "records": [record]},
            payload(next_row, date="2026-07-14"),
            payload(None),
        )

        self.assertEqual(updated["records"][0]["originalStrategy"], original)
        notes = updated["records"][0]["notes"]["systemNotes"]
        self.assertTrue(any("再次出現新觀察訊號" in note["text"] for note in notes))

    def test_target_hit_moves_to_ended(self):
        record = create_tracking_record("台股", base_row())
        completed = update_tracking_status(
            record,
            base_row(
                strategyAsOfDate="2026-07-14",
                date="2026-07-14",
                close=128,
                high=131,
                low=126,
            ),
            "ATTACK",
            "2026-07-14",
        )

        self.assertEqual(completed["trackingStatus"], "TARGET_HIT")
        self.assertEqual(completed["result"], "目標達成")

    def test_defense_downgrades_breakout_signal(self):
        record = create_tracking_record("台股", base_row())
        defense = update_tracking_status(
            record,
            base_row(
                strategyAsOfDate="2026-07-14",
                date="2026-07-14",
                adjustedSignal="",
                close=103,
                high=104,
                low=101,
            ),
            "DEFENSE",
            "2026-07-14",
        )

        self.assertEqual(
            defense["latestStatus"]["adjustedSignal"],
            "取消進場，降級觀察",
        )
        self.assertIn("不追價", defense["latestStatus"]["cashAction"])

    def test_expires_after_more_than_15_tracking_days_without_breakout(self):
        record = create_tracking_record("台股", base_row(close=96, high=97, low=95.5, safeScore=65))
        record["trackingDates"] = [f"2026-07-{day:02d}" for day in range(1, 16)]
        expired = update_tracking_status(
            record,
            base_row(
                strategyAsOfDate="2026-07-20",
                date="2026-07-20",
                close=96,
                high=97,
                low=95.5,
                safeScore=65,
                strictOk=False,
                grade="B",
            ),
            "WATCH",
            "2026-07-20",
        )

        self.assertEqual(expired["trackingStatus"], "EXPIRED")
        self.assertEqual(expired["result"], "觀察過期")


if __name__ == "__main__":
    unittest.main()

