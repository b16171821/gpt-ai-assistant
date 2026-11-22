"""阿波五燈交易系統 1.0

Backend and the published JSON are the only grading implementation.
The static site displays these fields and must not grade them again.

Indicators and returns use split-adjusted prices. Entry, stop, target,
neckline and the official close stay in raw trading prices. Volume ratios
use the previous 20 completed sessions (today is excluded). A stock below
the neckline or the prior high is not treated as extended.

Missing inputs are 「未核實」 and do not count as passed. Theme keywords and
a 60-day gain of at least 30% are tags, not lights.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

SYSTEM_NAME = "apo-five-lights-1.0"
SYSTEM_LABEL = "阿波五燈交易系統 1.0"

ADX_PERIOD = 14
ADX_MIN = 25.0
VOLUME_LOOKBACK = 20
VOLUME_BREAKOUT_MULTIPLE = 2.0
PULLBACK_VOLUME_MIN = 0.4
PULLBACK_VOLUME_MAX = 1.2
HIGH_VOLUME_BLACK_MULTIPLE = 1.2
LONG_BLACK_BODY_PCT = 0.04
UPPER_SHADOW_MAX_PCT = 0.03
BREAKOUT_LOOKBACK = 10
NECKLINE_LOOKBACK = 20
PRIOR_HIGH_LOOKBACK = 60
MA_FAST = 20
MA_MID = 60
MA_SLOW = 240
MIN_BARS = 60
RETURN_LOOKBACK = 60

EXTENSION_ABOVE_NECKLINE_PCT = 5.0
EXTENSION_ABOVE_PRIOR_HIGH_PCT = 10.0
INVALIDATION_PCT = 0.02
BREAKOUT_CHASE_PCT = 0.03
PULLBACK_TOUCH_PCT = 0.01
PULLBACK_HOLD_PCT = 0.005
OVEREXTENSION_MA20_PCT = 15.0
OVEREXTENSION_MA60_PCT = 30.0
STRONG_STOCK_RISE60 = 30.0

STOP_REFERENCE_PCT = 0.04
TARGET_REFERENCE_PCT = 0.15
MIN_REWARD_RISK = 2.0
TRAIL_TRIGGER_PCT = 0.10
TRAIL_GAP_PCT = 0.04
STOP_BAND_MIN = 0.015
STOP_BAND_MAX = 0.08
STOP_STRUCTURAL_MAX_WHEN_TIGHT = 0.12
STOP_STRUCTURAL_MIN_WHEN_WIDE = 0.01
ATR_TOO_TIGHT = 0.03
ATR_TOO_WIDE = 0.008

FEE_RATE = 0.001425
FEE_DISCOUNT = 1.0
SELL_TAX_RATE = 0.003
RISK_FRACTION = 0.01
DEFAULT_CAPITAL = 1_000_000.0
TW_LOT_SIZE = 1000
US_LOT_SIZE = 1
LIQUIDITY_PARTICIPATION = 0.05
TW_MIN_TURNOVER = 50_000_000.0
TW_MIN_AVG_TURNOVER20 = 30_000_000.0
US_MIN_AVG_TURNOVER20 = 20_000_000.0

RATING_RANK = {
    "待執行名單": 5,
    "高優先觀察": 4,
    "觀察": 3,
    "不符": 1,
    "未核實": 0,
}

HISTORY_COLUMNS = [
    "date",
    "code",
    "name",
    "market",
    "strategySystem",
    "rating",
    "rawRating",
    "lightsPassed",
    "score",
    "grade",
    "strictOk",
    "close",
    "observationEntry",
    "stopLoss",
    "target",
    "riskRewardRatio",
    "neckline",
    "pattern",
    "stage",
    "priceLocation",
    "volume",
    "volumeRatio",
    "adx",
    "plusDI",
    "minusDI",
    "rise60",
    "tags",
]


def sf(value, default=0.0):
    try:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return default
        if pd.isna(value):
            return default
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number


def sr(value, digits=2):
    number = sf(value, default=math.nan)
    if not math.isfinite(number):
        return None
    return round(number, digits)


def _finite_series(series):
    return pd.to_numeric(series, errors="coerce").astype(float)


def directional_moves(high, low, close):
    """Wilder +DM / -DM. Equal moves, including outside bars, zero both sides."""
    high = _finite_series(high)
    low = _finite_series(low)
    close = _finite_series(close)
    up = high.diff()
    down = -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    true_range = pd.concat(
        [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()],
        axis=1,
    ).max(axis=1)
    plus_dm.iloc[0] = np.nan
    minus_dm.iloc[0] = np.nan
    return plus_dm, minus_dm, true_range


def wilder_sum(series, period):
    values = [sf(value, default=math.nan) for value in series.tolist()]
    out = [math.nan] * len(values)
    start = next((i for i, value in enumerate(values) if math.isfinite(value)), None)
    if start is None or start + period > len(values):
        return pd.Series(out, index=series.index, dtype=float)
    seed_at = start + period - 1
    window = values[start:seed_at + 1]
    if any(not math.isfinite(value) for value in window):
        return pd.Series(out, index=series.index, dtype=float)
    out[seed_at] = float(sum(window))
    for index in range(seed_at + 1, len(values)):
        current = values[index]
        previous = out[index - 1]
        if not math.isfinite(current) or not math.isfinite(previous):
            out[index] = math.nan
            continue
        out[index] = previous - (previous / period) + current
    return pd.Series(out, index=series.index, dtype=float)


def wilder_average(series, period):
    values = [sf(value, default=math.nan) for value in series.tolist()]
    out = [math.nan] * len(values)
    start = next((i for i, value in enumerate(values) if math.isfinite(value)), None)
    if start is None or start + period > len(values):
        return pd.Series(out, index=series.index, dtype=float)
    seed_at = start + period - 1
    window = values[start:seed_at + 1]
    if any(not math.isfinite(value) for value in window):
        return pd.Series(out, index=series.index, dtype=float)
    out[seed_at] = float(sum(window) / period)
    for index in range(seed_at + 1, len(values)):
        current = values[index]
        previous = out[index - 1]
        if not math.isfinite(current) or not math.isfinite(previous):
            out[index] = math.nan
            continue
        out[index] = ((previous * (period - 1)) + current) / period
    return pd.Series(out, index=series.index, dtype=float)


def wilder_dmi(df, period=ADX_PERIOD):
    """Return ADX, +DI and -DI using Wilder smoothing."""
    plus_dm, minus_dm, true_range = directional_moves(df["High"], df["Low"], df["Close"])
    smooth_tr = wilder_sum(true_range, period)
    smooth_plus = wilder_sum(plus_dm, period)
    smooth_minus = wilder_sum(minus_dm, period)
    plus_di = 100 * smooth_plus / smooth_tr.replace(0, np.nan)
    minus_di = 100 * smooth_minus / smooth_tr.replace(0, np.nan)
    di_sum = (plus_di + minus_di).replace(0, np.nan)
    dx = (plus_di - minus_di).abs() / di_sum * 100
    adx = wilder_average(dx, period)
    atr = smooth_tr / period
    return pd.DataFrame(
        {"ADX": adx, "PLUS_DI": plus_di, "MINUS_DI": minus_di, "ATR": atr},
        index=df.index,
    )


def prepare_prices(df):
    """Adjusted OHLC for indicators. Raw OHLC is kept for traded prices.

    A missing adjusted close (the official bar is raw) reuses the previous
    adjustment factor instead of pretending the session was unadjusted.
    """
    frame = df.copy()
    frame.index = pd.to_datetime(frame.index)
    raw_close = _finite_series(frame["Close"])
    raw_open = _finite_series(frame["Open"]) if "Open" in frame.columns else raw_close.copy()
    raw_high = _finite_series(frame["High"]) if "High" in frame.columns else raw_close.copy()
    raw_low = _finite_series(frame["Low"]) if "Low" in frame.columns else raw_close.copy()
    if "Adj Close" in frame.columns:
        adjusted = _finite_series(frame["Adj Close"])
    else:
        adjusted = raw_close.copy()
    factor = adjusted / raw_close.replace(0, np.nan)
    factor = factor.replace([np.inf, -np.inf], np.nan).ffill().fillna(1.0)
    frame["Factor"] = factor
    frame["RawOpen"] = raw_open
    frame["RawHigh"] = raw_high
    frame["RawLow"] = raw_low
    frame["RawClose"] = raw_close
    frame["AdjOpen"] = raw_open * factor
    frame["AdjHigh"] = raw_high * factor
    frame["AdjLow"] = raw_low * factor
    frame["AdjClose"] = raw_close * factor
    if "Volume" in frame.columns:
        frame["Volume"] = _finite_series(frame["Volume"]).fillna(0)
    else:
        frame["Volume"] = 0.0
    return frame


def _light(light_id, name, state, detail):
    return {
        "id": light_id,
        "name": name,
        "state": state,
        "passed": state == "通過",
        "detail": detail,
    }


def overall_rating(lights):
    states = [light["state"] for light in lights]
    passed = sum(1 for light in lights if light["passed"])
    if states and all(state == "未核實" for state in states):
        rating = "未核實"
    elif passed >= 5:
        rating = "待執行名單"
    elif passed == 4:
        rating = "高優先觀察"
    elif passed == 3:
        rating = "觀察"
    else:
        rating = "不符"
    return rating, passed


def round_trip_risk_per_share(entry, stop, fee_rate=FEE_RATE, fee_discount=FEE_DISCOUNT, sell_tax_rate=SELL_TAX_RATE):
    """Cash lost per share if the close reaches the stop, including both fees and the sell tax."""
    entry = sf(entry)
    stop = sf(stop)
    if entry <= 0 or stop <= 0 or stop >= entry:
        return None
    buy_fee = entry * fee_rate * fee_discount
    sell_fee = stop * fee_rate * fee_discount
    sell_tax = stop * sell_tax_rate
    return (entry - stop) + buy_fee + sell_fee + sell_tax


def net_reward_risk(entry, stop, target, fee_rate=FEE_RATE, fee_discount=FEE_DISCOUNT, sell_tax_rate=SELL_TAX_RATE):
    entry = sf(entry)
    stop = sf(stop)
    target = sf(target)
    if not (stop < entry < target):
        return 0.0, 0.0, 0.0
    risk = round_trip_risk_per_share(entry, stop, fee_rate, fee_discount, sell_tax_rate)
    reward = (target - entry) - (entry * fee_rate * fee_discount) - (target * fee_rate * fee_discount) - (target * sell_tax_rate)
    if risk is None or risk <= 0 or reward <= 0:
        return 0.0, sf(risk), reward
    return reward / risk, risk, reward


def position_size(
    capital,
    entry,
    stop,
    lot_size,
    avg_turnover=None,
    fee_rate=FEE_RATE,
    fee_discount=FEE_DISCOUNT,
    sell_tax_rate=SELL_TAX_RATE,
    risk_fraction=RISK_FRACTION,
    max_position_pct=1.0,
    participation=LIQUIDITY_PARTICIPATION,
):
    """Shares = floor( (capital × risk) / cost-adjusted risk per share ) to the lot."""
    capital = sf(capital)
    entry = sf(entry)
    stop = sf(stop)
    lot = int(lot_size or 1)
    if lot < 1:
        lot = 1
    result = {
        "capital": capital,
        "riskFraction": risk_fraction,
        "entry": entry,
        "stop": stop,
        "lotSize": lot,
        "shares": 0,
        "sharesBeforeLot": 0,
        "riskBudget": 0.0,
        "riskPerShare": None,
        "liquidityCapped": False,
        "reason": "",
    }
    if capital <= 0 or entry <= 0 or stop <= 0:
        result["reason"] = "資金或價格無效"
        return result
    if stop >= entry:
        result["reason"] = "停損必須低於進場價"
        return result
    per_share = round_trip_risk_per_share(entry, stop, fee_rate, fee_discount, sell_tax_rate)
    risk_budget = capital * risk_fraction
    result["riskBudget"] = risk_budget
    result["riskPerShare"] = per_share
    if per_share is None or per_share <= 0 or risk_budget <= 0:
        result["reason"] = "風險額度不足"
        return result
    raw_shares = risk_budget / per_share
    buy_cash = entry * (1 + fee_rate * fee_discount)
    if max_position_pct is not None and sf(max_position_pct) > 0 and buy_cash > 0:
        raw_shares = min(raw_shares, (capital * sf(max_position_pct)) / buy_cash)
    turnover = sf(avg_turnover)
    if turnover > 0 and entry > 0:
        liquid_shares = (turnover * participation) / entry
        if raw_shares > liquid_shares:
            result["liquidityCapped"] = True
            raw_shares = liquid_shares
    result["sharesBeforeLot"] = int(math.floor(raw_shares)) if raw_shares > 0 else 0
    shares = int(math.floor(raw_shares / lot) * lot) if raw_shares > 0 else 0
    result["shares"] = shares
    if shares <= 0:
        result["reason"] = "依張數或流動性向下取整後為 0"
    return result


def _to_raw(level, factor):
    factor = sf(factor, default=1.0)
    if factor == 0:
        return sf(level, default=math.nan)
    return sf(level, default=math.nan) / factor


def _choose_stop(close, structural, atr_pct):
    close = sf(close)
    structural = sf(structural, default=math.nan)
    percent_stop = close * (1 - STOP_REFERENCE_PCT) if close > 0 else math.nan
    too_tight = math.isfinite(atr_pct) and atr_pct >= ATR_TOO_TIGHT
    too_wide = math.isfinite(atr_pct) and atr_pct <= ATR_TOO_WIDE
    flag = "4%停損相對波動偏緊" if too_tight else ("4%停損相對波動偏寬" if too_wide else "")

    def distance(stop):
        if not math.isfinite(stop) or close <= 0 or stop >= close:
            return None
        return (close - stop) / close

    def in_band(stop):
        gap = distance(stop)
        return gap is not None and STOP_BAND_MIN <= gap <= STOP_BAND_MAX

    structural_gap = distance(structural)
    if in_band(structural):
        chosen, why = structural, "結構停損位於合理區間，採用結構停損"
    elif in_band(percent_stop):
        chosen, why = percent_stop, "結構停損不在合理區間，採用 -4% 參考停損"
    elif (
        too_tight
        and structural_gap is not None
        and structural_gap <= STOP_STRUCTURAL_MAX_WHEN_TIGHT
    ):
        chosen, why = structural, "波動使 4% 過緊，改用仍低於現價的結構停損"
    elif (
        too_wide
        and structural_gap is not None
        and structural_gap >= STOP_STRUCTURAL_MIN_WHEN_WIDE
    ):
        chosen, why = structural, "波動使 4% 過寬，改用結構停損"
    elif distance(percent_stop) is not None:
        chosen, why = percent_stop, "沒有可用的結構停損，採用 -4% 參考停損"
    else:
        chosen, why = math.nan, "停損無法設在現價之下"
    return chosen, why, flag, too_tight, too_wide


def _pattern_frame(frame):
    close = frame["AdjClose"]
    high = frame["AdjHigh"]
    low = frame["AdjLow"]
    open_ = frame["AdjOpen"]
    volume = frame["Volume"]
    prev_high = high.shift(1).rolling(NECKLINE_LOOKBACK).max()
    prev_low = low.shift(1).rolling(NECKLINE_LOOKBACK).min()
    prev_volume = volume.shift(1).rolling(VOLUME_LOOKBACK).mean()
    body = (open_ - close) / close.replace(0, np.nan)
    long_black = (close < open_) & (body >= LONG_BLACK_BODY_PCT)
    high_volume_black = long_black & (volume >= prev_volume * HIGH_VOLUME_BLACK_MULTIPLE)
    upper = (high - pd.concat([close, open_], axis=1).max(axis=1)) / close.replace(0, np.nan)
    breakout = (
        (close >= prev_high)
        & (volume >= prev_volume * VOLUME_BREAKOUT_MULTIPLE)
        & ~high_volume_black.fillna(False)
        & (upper <= UPPER_SHADOW_MAX_PCT)
    )
    return {
        "prev_high": prev_high,
        "prev_low": prev_low,
        "prev_volume": prev_volume,
        "breakout": breakout.fillna(False),
        "high_volume_black": high_volume_black.fillna(False),
        "long_black": long_black.fillna(False),
    }


def flatten_price_columns(df):
    """yfinance sometimes returns a MultiIndex even for one symbol."""
    if df is None or not hasattr(df, "columns") or not isinstance(df.columns, pd.MultiIndex):
        return df
    flat = df.copy()
    flat.columns = flat.columns.get_level_values(-1)
    if flat.columns.duplicated().any():
        flat = flat.loc[:, ~flat.columns.duplicated()]
    return flat


def analyze_ohlcv(
    df,
    *,
    market="台股",
    symbol="",
    name="",
    theme_text="",
    keywords=None,
    lot_size=None,
    min_turnover=None,
    min_avg_turnover=None,
    capital=DEFAULT_CAPITAL,
):
    """Grade one symbol from completed daily bars. Returns None when MA60 cannot exist."""
    df = flatten_price_columns(df)
    if df is None or getattr(df, "empty", True) or "Close" not in getattr(df, "columns", []):
        return None
    frame = prepare_prices(df)
    frame = frame.dropna(subset=["RawClose"])
    if len(frame) < MIN_BARS:
        return None

    indicator_source = pd.DataFrame(
        {"High": frame["AdjHigh"], "Low": frame["AdjLow"], "Close": frame["AdjClose"]},
        index=frame.index,
    )
    dmi = wilder_dmi(indicator_source, ADX_PERIOD)
    adj_close = frame["AdjClose"]
    ma20 = adj_close.rolling(MA_FAST).mean()
    ma60 = adj_close.rolling(MA_MID).mean()
    ma240 = adj_close.rolling(MA_SLOW).mean()
    factor = sf(frame["Factor"].iloc[-1], 1.0) or 1.0
    raw_close = sf(frame["RawClose"].iloc[-1])
    raw_open = sf(frame["RawOpen"].iloc[-1], raw_close)
    raw_high = sf(frame["RawHigh"].iloc[-1], raw_close)
    raw_low = sf(frame["RawLow"].iloc[-1], raw_close)
    if raw_close <= 0:
        return None

    def raw_level(series, offset=1):
        value = sf(series.iloc[-offset], default=math.nan)
        if not math.isfinite(value):
            return None
        return value / factor

    pattern = _pattern_frame(frame)
    prev_volume = sf(pattern["prev_volume"].iloc[-1], default=math.nan)
    volume = sf(frame["Volume"].iloc[-1])
    volume_ratio = volume / prev_volume if prev_volume and math.isfinite(prev_volume) and prev_volume > 0 else None
    volume_unverified = volume_ratio is None

    recent = pattern["breakout"].iloc[-(BREAKOUT_LOOKBACK + 1):-1]
    recent_breakout = bool(recent.any()) if len(recent) else False
    locked_neck_adj = None
    if recent_breakout:
        locked_neck_adj = sf(pattern["prev_high"].loc[recent[recent].index[-1]], default=math.nan)
    today_neck_adj = sf(pattern["prev_high"].iloc[-1], default=math.nan)
    breakout_today = bool(pattern["breakout"].iloc[-1])
    high_volume_black = bool(pattern["high_volume_black"].iloc[-1])

    if breakout_today and math.isfinite(today_neck_adj):
        neck_adj = today_neck_adj
        pattern_name = "收盤突破前20日高點"
        stage = "突破確認"
    elif recent_breakout and locked_neck_adj and math.isfinite(locked_neck_adj):
        neck_adj = locked_neck_adj
        pattern_name = None
        stage = "未形成"
    elif math.isfinite(today_neck_adj):
        neck_adj = today_neck_adj
        pattern_name = None
        stage = "未形成"
    else:
        neck_adj = math.nan
        pattern_name = None
        stage = "未形成"

    neckline = neck_adj / factor if math.isfinite(neck_adj) and factor else None
    invalidation = neckline * (1 - INVALIDATION_PCT) if neckline else None
    adj_low = sf(frame["AdjLow"].iloc[-1])
    adj_close_now = sf(frame["AdjClose"].iloc[-1])
    pullback = False
    if (
        recent_breakout
        and not breakout_today
        and neckline
        and volume_ratio is not None
        and not high_volume_black
    ):
        pullback = (
            adj_low <= neck_adj * (1 + PULLBACK_TOUCH_PCT)
            and adj_low >= neck_adj * (1 - INVALIDATION_PCT)
            and adj_close_now >= neck_adj * (1 - PULLBACK_HOLD_PCT)
            and PULLBACK_VOLUME_MIN <= volume_ratio <= PULLBACK_VOLUME_MAX
        )
        if pullback:
            pattern_name = "突破後縮量回測支撐"
            stage = "回踩確認"

    if high_volume_black:
        pattern_name = "高量長黑"
        stage = "未形成"

    if pattern_name in {None, "高量長黑"}:
        display_pattern = "未形成" if pattern_name != "高量長黑" else "高量長黑"
    else:
        display_pattern = pattern_name

    if breakout_today and neckline:
        zone_low = neckline
        zone_high = neckline * (1 + BREAKOUT_CHASE_PCT)
    elif pullback and neckline and invalidation:
        zone_low = invalidation
        zone_high = neckline * (1 + PULLBACK_TOUCH_PCT)
    else:
        zone_low = None
        zone_high = None
    in_zone = bool(zone_low is not None and zone_high is not None and zone_low <= raw_close <= zone_high)

    prior_high_adj = sf(frame["AdjHigh"].iloc[:-1].tail(PRIOR_HIGH_LOOKBACK).max(), default=math.nan)
    prior_high = prior_high_adj / factor if math.isfinite(prior_high_adj) and factor else None
    distance_neck = ((raw_close - neckline) / neckline * 100) if neckline else None
    distance_high = ((raw_close - prior_high) / prior_high * 100) if prior_high else None
    extended_above = bool(
        (distance_neck is not None and distance_neck > EXTENSION_ABOVE_NECKLINE_PCT)
        or (distance_high is not None and distance_high > EXTENSION_ABOVE_PRIOR_HIGH_PCT)
    )
    broke_below = bool(neckline and raw_close < neckline * (1 - INVALIDATION_PCT))
    if extended_above:
        price_location = "延伸在壓力之上"
    elif broke_below:
        price_location = "跌破確認價"
    else:
        price_location = "區間內"

    ma20_raw = raw_level(ma20)
    ma60_raw = raw_level(ma60)
    ma240_value = sf(ma240.iloc[-1], default=math.nan)
    ma240_raw = (ma240_value / factor) if math.isfinite(ma240_value) else None
    above_fast = bool(ma20_raw and raw_close > ma20_raw and ma60_raw and raw_close > ma60_raw and ma20_raw > ma60_raw)
    extension_ma20 = ((raw_close - ma20_raw) / ma20_raw * 100) if ma20_raw else None
    extension_ma60 = ((raw_close - ma60_raw) / ma60_raw * 100) if ma60_raw else None
    overextended = bool(
        (extension_ma20 is not None and extension_ma20 > OVEREXTENSION_MA20_PCT)
        or (extension_ma60 is not None and extension_ma60 > OVEREXTENSION_MA60_PCT)
    )
    if ma20_raw is None or ma60_raw is None:
        long_trend = _light("L1", "長期趨勢", "未核實", "MA20 或 MA60 不足，長期趨勢不視為通過")
    elif not above_fast:
        long_trend = _light("L1", "長期趨勢", "未通過", "收盤未同時站上 MA20、MA60，或短均線尚未多頭排列")
    elif ma240_raw is None:
        long_trend = _light("L1", "長期趨勢", "未核實", "MA240 歷史不足，不自動通過長期趨勢")
    elif not (raw_close > ma240_raw and ma60_raw > ma240_raw):
        long_trend = _light("L1", "長期趨勢", "未通過", "收盤或均線結構未站上 MA240")
    elif overextended:
        long_trend = _light("L1", "長期趨勢", "等待", "趨勢成立但離均線過遠，等待而不是出場訊號")
    else:
        long_trend = _light("L1", "長期趨勢", "通過", "收盤站上 MA20、MA60、MA240，且均線多頭排列")

    adx = sf(dmi["ADX"].iloc[-1], default=math.nan)
    plus_di = sf(dmi["PLUS_DI"].iloc[-1], default=math.nan)
    minus_di = sf(dmi["MINUS_DI"].iloc[-1], default=math.nan)
    atr = sf(dmi["ATR"].iloc[-1], default=math.nan)
    atr_pct = (atr / factor) / raw_close if math.isfinite(atr) and raw_close else math.nan
    if not all(math.isfinite(value) for value in (adx, plus_di, minus_di)):
        strength = _light("L2", "趨勢強度", "未核實", "ADX 或 DI 不足")
    elif adx >= ADX_MIN and plus_di > minus_di:
        strength = _light("L2", "趨勢強度", "通過", f"ADX {adx:.1f}，+DI 高於 -DI")
    else:
        strength = _light("L2", "趨勢強度", "未通過", f"需要 ADX>={ADX_MIN:.0f} 且 +DI 高於 -DI")

    if volume_unverified:
        volume_light = _light("L3", "成交量", "未核實", "沒有前 20 個完整交易日的均量")
    elif breakout_today and volume_ratio >= VOLUME_BREAKOUT_MULTIPLE:
        volume_light = _light("L3", "成交量", "通過", "突破日量能至少是前 20 日均量的 2 倍")
    elif pullback:
        volume_light = _light("L3", "成交量", "通過", "突破後 10 日內的縮量回測")
    else:
        volume_light = _light("L3", "成交量", "未通過", "不是 2 倍量突破，也不是已定義的縮量回測")

    if volume_unverified and not high_volume_black and display_pattern == "未形成":
        price_light = _light("L4", "價格型態", "未核實", "量能或頸線不足，無法確認型態")
    elif high_volume_black:
        price_light = _light("L4", "價格型態", "未通過", "高量長黑，型態失效")
    elif display_pattern == "收盤突破前20日高點":
        price_light = _light(
            "L4",
            "價格型態",
            "通過",
            f"確認價 {neckline:.4f}，失效價 {invalidation:.4f}，量比 {volume_ratio:.2f}",
        )
    elif display_pattern == "突破後縮量回測支撐":
        price_light = _light(
            "L4",
            "價格型態",
            "通過",
            f"回測確認價 {neckline:.4f}，失效價 {invalidation:.4f}，量比 {volume_ratio:.2f}",
        )
    else:
        price_light = _light("L4", "價格型態", "未通過", "沒有收盤突破，也沒有成功的支撐回測")

    stop, stop_why, vol_flag, _tight, _wide = _choose_stop(raw_close, invalidation if invalidation else math.nan, atr_pct)
    target = raw_close * (1 + TARGET_REFERENCE_PCT) if raw_close else None
    entry = raw_close if in_zone else None
    ratio, risk_cash, reward_cash = net_reward_risk(entry or raw_close, stop, target or 0)
    turnover = raw_close * volume
    avg_turnover = None
    if prev_volume and math.isfinite(prev_volume):
        previous_closes = frame["RawClose"].shift(1).tail(VOLUME_LOOKBACK)
        avg_turnover = sf((previous_closes * frame["Volume"].shift(1).tail(VOLUME_LOOKBACK)).mean(), default=math.nan)
        if not math.isfinite(avg_turnover):
            avg_turnover = None
    if market == "美股":
        turnover_floor = US_MIN_AVG_TURNOVER20 if min_avg_turnover is None else min_avg_turnover
        liquid = bool(avg_turnover and avg_turnover >= turnover_floor)
        lot = US_LOT_SIZE if lot_size is None else lot_size
    else:
        turnover_floor = TW_MIN_AVG_TURNOVER20 if min_avg_turnover is None else min_avg_turnover
        day_floor = TW_MIN_TURNOVER if min_turnover is None else min_turnover
        liquid = bool(turnover >= day_floor and avg_turnover and avg_turnover >= turnover_floor)
        lot = TW_LOT_SIZE if lot_size is None else lot_size

    if zone_low is None:
        risk_light = _light("L5", "風險報酬", "未通過", "沒有可執行進場區，不構成正式訊號")
    elif not in_zone:
        risk_light = _light("L5", "風險報酬", "等待", "現價不在進場區，不是正式訊號")
    elif not math.isfinite(sf(stop, math.nan)) or sf(stop) >= raw_close:
        risk_light = _light("L5", "風險報酬", "未通過", "停損必須低於現價")
    elif not liquid:
        risk_light = _light("L5", "風險報酬", "未通過", "流動性不足，無法依計畫進出")
    elif ratio < MIN_REWARD_RISK:
        risk_light = _light("L5", "風險報酬", "未通過", f"扣成本後風報比 {ratio:.2f}，低於 {MIN_REWARD_RISK:.0f}")
    else:
        detail = stop_why
        if vol_flag:
            detail = f"{detail}；{vol_flag}"
        risk_light = _light("L5", "風險報酬", "通過", detail)

    lights = [long_trend, strength, volume_light, price_light, risk_light]
    rating, passed = overall_rating(lights)
    strict_ok = rating == "待執行名單"
    if len(adj_close) > RETURN_LOOKBACK and sf(adj_close.iloc[-1 - RETURN_LOOKBACK]) > 0:
        rise60 = (sf(adj_close.iloc[-1]) / sf(adj_close.iloc[-1 - RETURN_LOOKBACK]) - 1) * 100
    else:
        rise60 = None
    tags = []
    keywords = keywords or []
    theme = theme_text or ""
    if theme and any(str(word).lower() in theme.lower() for word in keywords if str(word)):
        tags.append("題材")
    if rise60 is not None and rise60 >= STRONG_STOCK_RISE60:
        tags.append("強勢股候選")

    sizing = position_size(
        capital,
        entry or raw_close,
        stop if math.isfinite(sf(stop, math.nan)) else 0,
        lot,
        avg_turnover=avg_turnover,
    )
    exclusions = []
    if price_location == "延伸在壓力之上":
        exclusions.append("延伸在壓力之上")
    if price_location == "跌破確認價":
        exclusions.append("跌破確認價")
    for light in lights:
        if light["state"] == "未核實":
            exclusions.append(f"{light['name']}未核實")
        elif light["state"] == "等待":
            exclusions.append(light["detail"])
        elif not light["passed"]:
            exclusions.append(light["detail"])
    if any(light["state"] == "未核實" for light in lights):
        status = f"{rating}（含未核實，未核實不算通過）"
    else:
        status = rating
    action = {
        "待執行名單": "五燈通過且價格在進場區。仍須大盤濾網允許進場。",
        "高優先觀察": "四燈通過。缺的那一燈未過或未核實，不是正式訊號。",
        "觀察": "三燈通過，只觀察。",
        "不符": "通過燈數不足。",
        "未核實": "關鍵資料不足，不計入通過。",
    }[rating]
    buy_low = zone_low if zone_low is not None else neckline
    buy_high = zone_high if zone_high is not None else (neckline * (1 + BREAKOUT_CHASE_PCT) if neckline else None)
    observation = raw_close if in_zone else (neckline if neckline else None)
    risk_pct = (risk_cash / raw_close * 100) if raw_close and risk_cash else None
    reward_pct = (reward_cash / raw_close * 100) if raw_close and reward_cash else None
    strategy_date = str(frame.index[-1].date())
    volatility20 = sf(adj_close.pct_change().rolling(20).std().iloc[-1] * 100, default=math.nan)

    return {
        "date": strategy_date,
        "strategyAsOfDate": strategy_date,
        "strategySystem": SYSTEM_NAME,
        "strategyLabel": SYSTEM_LABEL,
        "backtestStatus": "尚未回測",
        "priceType": "收盤確認價",
        "priceBasis": "指標與報酬用還原價；進場、停損、目標與頸線用原始成交價",
        "exitPriceBasis": "close",
        "market": market,
        "code": symbol,
        "name": name or symbol,
        "close": sr(raw_close),
        "open": sr(raw_open),
        "high": sr(raw_high),
        "low": sr(raw_low),
        "ma5": sr(raw_level(adj_close.rolling(5).mean())),
        "ma10": sr(raw_level(adj_close.rolling(10).mean())),
        "ma20": sr(ma20_raw) if ma20_raw else None,
        "ma60": sr(ma60_raw) if ma60_raw else None,
        "ma240": sr(ma240_raw) if ma240_raw else None,
        "ma200": sr(raw_level(adj_close.rolling(200).mean())),
        "rise60": sr(rise60) if rise60 is not None else None,
        "changePct": sr(sf(adj_close.pct_change().iloc[-1]) * 100),
        "nearLimitUp": bool(sf(adj_close.pct_change().iloc[-1]) * 100 >= 8),
        "volume": int(volume),
        "avgVolume20": int(prev_volume) if prev_volume and math.isfinite(prev_volume) else 0,
        "volumeRatio": sr(volume_ratio) if volume_ratio is not None else None,
        "turnover": int(turnover),
        "avgTurnover20": int(avg_turnover) if avg_turnover else 0,
        "volatility20Pct": sr(volatility20) if math.isfinite(volatility20) else None,
        "liquidityOk": bool(liquid),
        "adx": sr(adx) if math.isfinite(adx) else None,
        "plusDI": sr(plus_di) if math.isfinite(plus_di) else None,
        "minusDI": sr(minus_di) if math.isfinite(minus_di) else None,
        "atrPct": sr(atr_pct * 100) if math.isfinite(atr_pct) else None,
        "pattern": display_pattern,
        "stage": stage,
        "neckline": sr(neckline) if neckline else None,
        "confirmationLevel": sr(neckline) if neckline else None,
        "invalidationLevel": sr(invalidation) if invalidation else None,
        "support": sr(invalidation) if invalidation else None,
        "volumeEvidence": None if volume_ratio is None else f"前20日均量不含今日，量比 {sr(volume_ratio)}",
        "target": sr(target) if target else None,
        "entryBreakout": sr(neckline) if neckline else None,
        "entryPullback": sr(neckline) if neckline else None,
        "chaseRangeLow": sr(buy_low) if buy_low else None,
        "chaseRangeHigh": sr(buy_high) if buy_high else None,
        "buyLow": sr(buy_low) if buy_low else None,
        "buyHigh": sr(buy_high) if buy_high else None,
        "observationEntry": sr(observation) if observation else None,
        "stopLoss": sr(stop) if math.isfinite(sf(stop, math.nan)) else None,
        "stopSelection": stop_why,
        "stopVolatilityFlag": vol_flag,
        "riskPct": sr(risk_pct) if risk_pct is not None else None,
        "rewardPct": sr(reward_pct) if reward_pct is not None else None,
        "riskRewardRatio": sr(ratio) if ratio else 0,
        "distanceFromNecklinePct": sr(distance_neck) if distance_neck is not None else None,
        "distanceFromPrevHighPct": sr(distance_high) if distance_high is not None else None,
        "priceLocation": price_location,
        "extendedAbove": bool(extended_above),
        "brokeBelow": bool(broke_below),
        "forbiddenChase": bool(extended_above),
        "nearTarget": False,
        "recentBreakout": bool(recent_breakout or breakout_today),
        "inEntryZone": bool(in_zone),
        "lights": lights,
        "lightsPassed": int(passed),
        "score": int(passed),
        "safeScore": int(passed * 20),
        "rawRating": rating,
        "rating": rating,
        "grade": rating,
        "category": display_pattern if display_pattern != "未形成" else rating,
        "strictOk": bool(strict_ok),
        "strictOkBase": bool(strict_ok),
        "status": status,
        "action": action,
        "planStatus": action,
        "reason": "；".join(light["name"] + light["state"] for light in lights),
        "exclusionReasons": exclusions,
        "tags": tags,
        "theme": theme,
        "themeHit": "題材" in tags,
        "strongStockCandidate": "強勢股候選" in tags,
        "positionCapital": capital,
        "positionShares": sizing["shares"],
        "positionSharesBeforeLot": sizing["sharesBeforeLot"],
        "positionLotSize": lot,
        "positionRiskPerShare": sr(sizing["riskPerShare"], 4) if sizing["riskPerShare"] else None,
        "positionNote": f"以資金 {int(capital)}、風險 {RISK_FRACTION:.0%}、手續費 {FEE_RATE:.4%}×折扣 {FEE_DISCOUNT}、賣出證交稅 {SELL_TAX_RATE:.1%} 試算",
        "feeRate": FEE_RATE,
        "feeDiscount": FEE_DISCOUNT,
        "sellTaxRate": SELL_TAX_RATE,
        "trailTriggerPct": TRAIL_TRIGGER_PCT * 100,
        "trailGapPct": TRAIL_GAP_PCT * 100,
        "confirmConditions": (
            "L1 收盤高於 MA20/MA60/MA240 且均線多頭，MA240 不足為未核實；"
            "L2 Wilder ADX>=25 且 +DI>-DI；"
            "L3 突破量>=前20個完整交易日均量2倍，或10日內縮量回測；"
            "L4 收盤突破或成功回測，並給出確認價、失效價與量能；"
            "L5 停損低於現價、扣成本後風報比>=2、價格在進場區。"
        ),
    }


def history_row(row):
    projected = {}
    for column in HISTORY_COLUMNS:
        value = row.get(column)
        if isinstance(value, (list, dict)):
            value = "|".join(str(item) for item in value)
        projected[column] = "" if value is None else value
    if not projected["code"]:
        projected["code"] = row.get("ticker") or ""
    if not projected["date"]:
        projected["date"] = str(row.get("strategyAsOfDate") or "")[:10]
    return projected


def append_history(path, rows):
    """Append this run and replace any earlier row with the same date and code."""
    import csv
    from pathlib import Path

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if path.exists() and path.stat().st_size:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            existing = list(csv.DictReader(handle))
    merged = {}
    order = []
    for raw in existing:
        key = (str(raw.get("date") or "")[:10], str(raw.get("code") or raw.get("ticker") or ""))
        if key not in merged:
            order.append(key)
        merged[key] = raw
    for row in rows:
        projected = history_row(row)
        key = (str(projected.get("date") or "")[:10], str(projected.get("code") or ""))
        if not key[0] or not key[1]:
            continue
        if key not in merged:
            order.append(key)
        merged[key] = projected
    fieldnames = list(HISTORY_COLUMNS)
    extras = []
    for raw in merged.values():
        for name in raw.keys():
            if name not in fieldnames and name not in extras:
                extras.append(name)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames + extras, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for key in sorted(order):
            writer.writerow(merged[key])
    return len(merged)


def gate_rating(raw_rating, regime):
    """Market regime may downgrade a formal signal. It does not turn a failed light on."""
    rating = raw_rating or "未核實"
    regime = regime or "ATTACK"
    if rating == "待執行名單" and regime == "WATCH":
        return "高優先觀察"
    if rating in {"待執行名單", "高優先觀察"} and regime in {"DEFENSE", "STOP"}:
        return "觀察"
    return rating
