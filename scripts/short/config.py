from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ShortConfig:
    version: str = 'short-v1.0-approved-2026-11-06'
    min_history: int = 120
    ma_fast: int = 20
    ma_slow: int = 60
    slope_days: int = 5
    trend_confirm_days: int = 2
    atr_period: int = 14
    pivot_side: int = 2
    platform_days: int = 20
    platform_max_width: float = 0.12
    platform_touches: int = 2
    touch_spacing: int = 3
    touch_atr: float = 0.5
    break_atr: float = 0.25
    break_volume: float = 1.5
    close_location_max: float = 0.4
    retest_days: int = 10
    rejection_days: int = 3
    rejection_atr: float = 0.1
    reclaim_atr: float = 0.25
    false_break_days: int = 3
    deep_break_atr: float = 2.0
    deep_ma_atr: float = 2.5
    deep_return_days: int = 5
    deep_return: float = -0.12
    zone_atr: float = 0.25
    break_stop_atr: float = 0.5
    rejection_stop_atr: float = 0.25
    minimum_rr: float = 2.0
    maximum_stop_pct: float = 6.0
    priority_score: int = 80
    reserve_score: int = 60
    observation_days: int = 10
    max_holding_days: int = 20
    action_cooldown_days: int = 5
    min_turnover_tw: float = 30_000_000
    min_turnover_us: float = 5_000_000
    maximum_adv_participation: float = 0.01
    earnings_before: int = 1
    earnings_after: int = 1

    def manifest(self):
        return asdict(self)


CONFIG = ShortConfig()
SIGNAL_LABELS = {
    'BEAR_TREND': '空頭趨勢形成',
    'PLATFORM_BREAK': '整理平台跌破',
    'FAILED_RETEST': '反彈不過原支撐',
    'MA_REJECTION': '均線附近反彈轉弱',
    'TOO_EXTENDED': '已跌深，禁止追空',
    'FALSE_BREAK': '站回原支撐，疑似假跌破',
    'NO_SETUP': '尚無完整空方型態',
}
ENTRY_SIGNALS = {'PLATFORM_BREAK', 'FAILED_RETEST', 'MA_REJECTION'}
