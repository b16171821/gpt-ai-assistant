import sys
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from update_strategy_tracking import (  # noqa: E402
    ACTIVE_STATUSES,
    NEW_SIGNAL_NOTICE,
    create_tracking_record,
    evaluateTrackingLifecycle,
    process_tracking,
    supplement_active_tracking_rows,
    update_tracking_status,
    valid_tracking_setup,
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
    @patch("update_strategy_tracking.download_price_frames", return_value={})
    @patch("update_strategy_tracking.download_official_tw_quotes")
    def test_official_tw_close_replaces_stale_tracking_row(
        self,
        official_quotes,
        _download_price_frames,
    ):
        record = create_tracking_record(
            "台股",
            base_row(
                code="3288",
                name="點晶",
                date="2026-07-28",
                strategyAsOfDate="2026-07-28",
                close=27.2,
                high=27.2,
                low=25.5,
                neckline=26.65,
                observationEntry=27.59,
                chaseRangeLow=27.18,
                chaseRangeHigh=28,
                stopLoss=25.85,
                target=38.95,
            ),
            {"strategyAsOfDate": "2026-07-29"},
        )
        official_quotes.return_value = {
            "3288": {
                "date": "2026-07-29",
                "open": 24.8,
                "high": 24.8,
                "low": 24.5,
                "close": 24.5,
                "volume": 106460,
                "source": "櫃買中心正式收盤",
            }
        }

        updated_payload = supplement_active_tracking_rows(
            {"records": [record]},
            "台股",
            payload(
                base_row(
                    code="3288",
                    name="點晶",
                    date="2026-07-28",
                    strategyAsOfDate="2026-07-28",
                    close=27.2,
                ),
                date="2026-07-29",
            ),
        )
        row = updated_payload["stocks"][0]

        self.assertEqual(row["strategyAsOfDate"], "2026-07-29")
        self.assertEqual(row["close"], 24.5)
        self.assertEqual(row["high"], 24.8)
        self.assertEqual(row["trackingPriceSource"], "櫃買中心正式收盤")

    def test_strategy_data_date_and_tracking_created_date_are_separate(self):
        record = create_tracking_record(
            "台股",
            base_row(date="2026-07-28", strategyAsOfDate="2026-07-28"),
            {"strategyAsOfDate": "2026-07-29"},
        )

        self.assertEqual(record["strategyDataDate"], "2026-07-28")
        self.assertEqual(record["trackingCreatedDate"], "2026-07-29")

    def test_invalid_setup_is_rejected_before_tracking(self):
        row = base_row(close=94, stopLoss=95)

        self.assertFalse(valid_tracking_setup(row))
        updated = process_tracking(
            {"meta": {}, "records": []},
            payload(row),
            payload(None),
        )
        self.assertEqual(updated["records"], [])

    def test_same_day_failed_record_is_reclassified_as_invalid_creation(self):
        record = create_tracking_record("台股", base_row(close=94, stopLoss=95))
        self.assertEqual(record["trackingStatus"], "FAILED")

        updated = process_tracking(
            {"meta": {}, "records": [record]},
            payload(None, date="2026-07-14"),
            payload(None),
        )
        migrated = updated["records"][0]

        self.assertEqual(migrated["trackingStatus"], "INVALID_AT_CREATION")
        self.assertEqual(migrated["endPriority"], "INVALID_AT_CREATION")
        self.assertIn("不計入正式追蹤成敗", migrated["notes"]["systemNotes"][-1]["text"])

    def test_older_market_row_does_not_overwrite_newer_tracking_state(self):
        record = create_tracking_record(
            "台股",
            base_row(date="2026-07-29", strategyAsOfDate="2026-07-29", close=103),
            {"strategyAsOfDate": "2026-07-29"},
        )
        original_latest = deepcopy(record["latestStatus"])
        stale_row = base_row(
            date="2026-07-28",
            strategyAsOfDate="2026-07-28",
            close=99,
        )

        updated = process_tracking(
            {"meta": {}, "records": [record]},
            payload(stale_row, date="2026-07-29"),
            payload(None),
        )
        tracked = updated["records"][0]

        self.assertEqual(tracked["lastUpdateDate"], "2026-07-29")
        self.assertEqual(tracked["latestStatus"]["latestClose"], original_latest["latestClose"])

    def test_stale_latest_status_uses_its_own_data_date(self):
        record = create_tracking_record(
            "台股",
            base_row(date="2026-07-29", strategyAsOfDate="2026-07-29", close=103),
            {"strategyAsOfDate": "2026-07-29"},
        )
        record["latestStatus"].pop("latestDataDate", None)
        record["latestStatus"]["latestClose"] = 94
        record["latestStatus"]["latestHigh"] = 96
        record["latestStatus"]["latestLow"] = 93
        record["lastUpdateDate"] = "2026-07-28"
        record["trackingDates"] = ["2026-07-28", "2026-07-29"]
        record["statusHistory"] = [
            {"date": "2026-07-29", "status": "BREAKOUT_CONFIRMED", "close": 103}
        ]

        updated = process_tracking(
            {"meta": {}, "records": [record]},
            payload(None, date="2026-07-29"),
            payload(None),
        )
        tracked = updated["records"][0]

        self.assertIn(tracked["trackingStatus"], ACTIVE_STATUSES)
        self.assertEqual(tracked["trackingStatus"], "BREAKOUT_CONFIRMED")
        self.assertEqual(tracked["latestStatus"]["latestDataDate"], "2026-07-29")
        self.assertEqual(tracked["latestStatus"]["latestClose"], 103)
        self.assertTrue(tracked["latestStatus"]["restoredFromStatusHistory"])

    def test_full_latest_status_is_preserved_in_status_history(self):
        record = create_tracking_record(
            "台股",
            base_row(date="2026-07-29", strategyAsOfDate="2026-07-29", close=103),
            {"strategyAsOfDate": "2026-07-29"},
        )
        newest = record["statusHistory"][-1]

        self.assertEqual(newest["date"], "2026-07-29")
        self.assertEqual(newest["latestStatus"]["latestDataDate"], "2026-07-29")
        self.assertEqual(newest["latestStatus"]["latestClose"], 103)
        self.assertEqual(
            newest["latestStatus"]["latestVolume"],
            record["latestStatus"]["latestVolume"],
        )

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
        self.assertEqual(failed["endedAt"], "2026-07-14")
        self.assertEqual(
            failed["latestStatus"]["riskWarning"],
            "原始策略已失敗，不可用新的策略卡延後停損。",
        )

    def test_new_signal_does_not_overwrite_original_strategy(self):
        record = create_tracking_record("台股", base_row())
        original = deepcopy(record["originalStrategy"])
        first_signal_date = record["firstSignalDate"]
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
        self.assertEqual(updated["records"][0]["firstSignalDate"], first_signal_date)
        self.assertEqual(
            updated["records"][0]["originalStrategy"]["originalStopLoss"],
            original["originalStopLoss"],
        )
        self.assertEqual(
            updated["records"][0]["originalStrategy"]["originalBuyZoneLow"],
            original["originalBuyZoneLow"],
        )
        self.assertEqual(
            updated["records"][0]["originalStrategy"]["originalBuyZoneHigh"],
            original["originalBuyZoneHigh"],
        )
        self.assertEqual(
            updated["records"][0]["originalStrategy"]["originalTarget"],
            original["originalTarget"],
        )
        notes = updated["records"][0]["notes"]["systemNotes"]
        self.assertTrue(any(NEW_SIGNAL_NOTICE == note["text"] for note in notes))

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

    def test_legacy_data_is_attached_without_overwriting_original_strategy(self):
        record = create_tracking_record("台股", base_row())
        original = deepcopy(record["originalStrategy"])
        first_signal_date = record["firstSignalDate"]
        snapshots = {
            "台股:3374": [
                {
                    "date": "2026-07-12",
                    "market": "台股",
                    "code": "3374",
                    "name": "精材",
                    "close": 90,
                    "buyLow": 91,
                    "buyHigh": 92,
                    "stopLoss": 80,
                    "target": 160,
                    "action": "舊策略訊號",
                }
            ]
        }
        locks = {
            "台股:3374": {
                "market": "台股",
                "code": "3374",
                "name": "精材",
                "createdAt": "2026-07-10",
                "originalBuyLow": 88,
                "originalBuyHigh": 89,
                "originalStopLoss": 79,
                "originalTarget": 170,
                "trackingStatus": "舊鎖定訊號",
            }
        }

        updated = process_tracking(
            {"meta": {}, "records": [record]},
            payload(None, date="2026-07-14"),
            payload(None),
            locks,
            snapshots,
        )
        migrated = updated["records"][0]

        self.assertEqual(migrated["originalStrategy"], original)
        self.assertEqual(migrated["firstSignalDate"], first_signal_date)
        self.assertEqual(migrated["notes"]["legacySnapshots"][0]["stopLoss"], 80)
        self.assertEqual(migrated["notes"]["legacyLock"]["originalStopLoss"], 79)

    def test_target_before_stop_finishes_as_target_hit(self):
        record = create_tracking_record("台股", base_row())
        evaluated = evaluateTrackingLifecycle(
            record,
            [
                {"date": "2026-07-14", "high": 110, "low": 100, "close": 108},
                {"date": "2026-07-16", "high": 131, "low": 126, "close": 128},
                {"date": "2026-07-23", "high": 97, "low": 93, "close": 94},
            ],
        )

        self.assertEqual(evaluated["trackingStatus"], "TARGET_HIT")
        self.assertEqual(evaluated["targetHitDate"], "2026-07-16")
        self.assertEqual(evaluated["stopLossHitDate"], "2026-07-23")
        self.assertEqual(evaluated["endedAt"], "2026-07-16")
        self.assertEqual(evaluated["endPriority"], "TARGET_FIRST")

        locked = update_tracking_status(
            evaluated,
            base_row(date="2026-07-24", strategyAsOfDate="2026-07-24", close=90, high=92),
            "ATTACK",
            "2026-07-24",
        )
        self.assertEqual(locked["trackingStatus"], "TARGET_HIT")
        self.assertEqual(locked["endedAt"], "2026-07-16")

    def test_stop_before_target_finishes_as_failed(self):
        record = create_tracking_record("台股", base_row())
        evaluated = evaluateTrackingLifecycle(
            record,
            [
                {"date": "2026-07-16", "high": 97, "low": 93, "close": 94},
                {"date": "2026-07-23", "high": 131, "low": 126, "close": 130},
            ],
        )

        self.assertEqual(evaluated["trackingStatus"], "FAILED")
        self.assertEqual(evaluated["endedAt"], "2026-07-16")
        self.assertEqual(evaluated["endPriority"], "STOP_FIRST")

    def test_same_day_target_and_stop_needs_review(self):
        record = create_tracking_record("台股", base_row())
        evaluated = evaluateTrackingLifecycle(
            record,
            [
                {"date": "2026-07-16", "high": 131, "low": 93, "close": 94},
            ],
        )

        self.assertEqual(evaluated["trackingStatus"], "NEED_REVIEW")
        self.assertEqual(evaluated["endedAt"], "2026-07-16")
        self.assertEqual(evaluated["endPriority"], "SAME_DAY_REVIEW")

    def test_never_hits_target_then_breaks_stop(self):
        record = create_tracking_record("台股", base_row())
        evaluated = evaluateTrackingLifecycle(
            record,
            [
                {"date": "2026-07-16", "high": 120, "low": 110, "close": 115},
                {"date": "2026-07-23", "high": 97, "low": 93, "close": 94},
            ],
        )

        self.assertEqual(evaluated["trackingStatus"], "FAILED")
        self.assertIsNone(evaluated["targetHitDate"])
        self.assertEqual(evaluated["stopLossHitDate"], "2026-07-23")

    def test_near_target_then_stop_records_review_note(self):
        record = create_tracking_record("台股", base_row())
        evaluated = evaluateTrackingLifecycle(
            record,
            [
                {"date": "2026-07-16", "high": 120, "low": 115, "close": 118},
                {"date": "2026-07-23", "high": 97, "low": 93, "close": 94},
            ],
        )

        self.assertEqual(evaluated["trackingStatus"], "FAILED")
        notes = evaluated["notes"]["systemNotes"]
        self.assertTrue(
            any(
                note["text"] == "追蹤期間曾接近原始目標，可檢討停利規則。"
                for note in notes
            )
        )

    def test_existing_failed_record_backfills_stop_date_from_latest_status(self):
        record = create_tracking_record("台股", base_row())
        record["trackingStatus"] = "FAILED"
        record["endedAt"] = "2026-07-29"
        record["endDate"] = "2026-07-29"
        record["lastUpdateDate"] = "2026-07-29"
        record["latestStatus"]["latestClose"] = 94
        record["latestStatus"]["latestHigh"] = 96
        record["latestStatus"]["latestLow"] = 93
        record["latestStatus"]["latestDataDate"] = "2026-07-29"

        updated = process_tracking(
            {"meta": {}, "records": [record]},
            payload(None, date="2026-07-29"),
            payload(None),
        )
        migrated = updated["records"][0]

        self.assertEqual(migrated["trackingStatus"], "FAILED")
        self.assertEqual(migrated["stopLossHitDate"], "2026-07-29")
        self.assertEqual(migrated["endPriority"], "STOP_FIRST")


if __name__ == "__main__":
    unittest.main()
