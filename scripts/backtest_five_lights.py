"""Backtest for 阿波五燈交易系統 1.0.

The walk uses only bars that have already closed. Neckline and the 20-day
volume average come from the shared engine, which shifts by one session.
A signal on day T is filled at the next session's open. The target and the
stop are both judged on later closes. Fees and the Taiwan sell tax are
subtracted.

This does not publish a win rate. A run on a current constituent list has
survivorship bias: names that disappeared are absent. Rate-cycle splits are
emitted only when a rate series is supplied.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import pandas as pd

import apo_five_lights as five

SURVIVORSHIP_CAVEAT = (
    "若輸入是目前仍存續的股票或觀察清單，已下市的股票不會出現，結果有倖存者偏差，"
    "不能當成系統的公開勝率。"
)
MAX_HOLD_DAYS = 20


def _prepare(df):
    frame = df.copy()
    frame.index = pd.to_datetime(frame.index)
    frame = frame.sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]
    return frame


def signal_on_prefix(frame, end, market="台股"):
    prefix = frame.iloc[: end + 1]
    row = five.analyze_ohlcv(prefix, market=market, symbol="BT", name="BT")
    if not row or row.get("rawRating") != "待執行名單":
        return None
    return row


def _regime(index_frame, day):
    if index_frame is None or index_frame.empty:
        return "未提供"
    history = index_frame[index_frame.index <= day]
    if len(history) < five.MA_MID:
        return "未提供"
    close = history["Close"].astype(float)
    ma20 = close.rolling(five.MA_FAST).mean().iloc[-1]
    ma60 = close.rolling(five.MA_MID).mean().iloc[-1]
    last = close.iloc[-1]
    if last > ma60 and ma20 > ma60:
        return "多頭"
    if last < ma60 and ma20 < ma60:
        return "空頭"
    return "區間"


def _rate_cycle(rate, day):
    if rate is None or rate.empty:
        return "未提供"
    history = rate[rate.index <= day].dropna()
    if len(history) <= five.RETURN_LOOKBACK:
        return "未提供"
    latest = float(history.iloc[-1])
    prior = float(history.iloc[-1 - five.RETURN_LOOKBACK])
    if latest > prior:
        return "利率上升"
    if latest < prior:
        return "利率下降"
    return "利率持平"


def run_backtest(frames, index_frame=None, rate_series=None, market="台股", max_hold=MAX_HOLD_DAYS):
    """frames: dict of symbol -> daily OHLCV DataFrame."""
    trades = []
    for symbol, raw in frames.items():
        frame = _prepare(raw)
        if len(frame) < five.MIN_BARS + 2:
            continue
        cursor = five.MIN_BARS - 1
        while cursor < len(frame) - 1:
            signal = signal_on_prefix(frame, cursor, market=market)
            if not signal:
                cursor += 1
                continue
            entry_bar = frame.iloc[cursor + 1]
            entry = float(entry_bar["Open"])
            if not math.isfinite(entry) or entry <= 0:
                cursor += 1
                continue
            stop = float(signal["stopLoss"])
            target = float(signal["target"])
            # Keep the signal's risk distances, translated onto the next open.
            stop_gap = float(signal["close"]) - stop
            target_gap = target - float(signal["close"])
            stop = entry - stop_gap
            target = entry + target_gap
            if not (stop < entry < target):
                cursor += 1
                continue
            exit_price = None
            exit_reason = None
            exit_at = None
            last_index = min(len(frame) - 1, cursor + 1 + max_hold)
            for offset in range(cursor + 1, last_index + 1):
                close = float(frame.iloc[offset]["Close"])
                if close >= target:
                    exit_price = close
                    exit_reason = "收盤達目標"
                    exit_at = frame.index[offset]
                    break
                if close <= stop:
                    exit_price = close
                    exit_reason = "收盤觸停損"
                    exit_at = frame.index[offset]
                    break
            if exit_price is None:
                exit_at = frame.index[last_index]
                exit_price = float(frame.iloc[last_index]["Close"])
                exit_reason = "持有到期"
            fee = five.FEE_RATE * five.FEE_DISCOUNT
            cost = entry * fee + exit_price * fee + exit_price * five.SELL_TAX_RATE
            pnl = (exit_price - entry) - cost
            day = frame.index[cursor]
            trades.append(
                {
                    "symbol": symbol,
                    "signalDate": str(day.date()),
                    "entryDate": str(frame.index[cursor + 1].date()),
                    "exitDate": str(pd.Timestamp(exit_at).date()),
                    "entry": round(entry, 4),
                    "exit": round(exit_price, 4),
                    "stop": round(stop, 4),
                    "target": round(target, 4),
                    "pnl": pnl,
                    "cost": cost,
                    "reason": exit_reason,
                    "regime": _regime(index_frame, day),
                    "rateCycle": _rate_cycle(rate_series, day),
                }
            )
            cursor = frame.index.get_loc(exit_at) + 1
            if not isinstance(cursor, int):
                cursor = int(cursor)

    return summarize_trades(trades)


def _avg(values):
    if not values:
        return None
    return sum(values) / len(values)


def _max_drawdown(pnls):
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def _bucket(trades):
    wins = [trade["pnl"] for trade in trades if trade["pnl"] > 0]
    losses = [trade["pnl"] for trade in trades if trade["pnl"] <= 0]
    pnls = [trade["pnl"] for trade in trades]
    avg_win = _avg(wins)
    avg_loss = _avg(losses)
    reward_risk = None
    if avg_win is not None and avg_loss not in (None, 0):
        reward_risk = avg_win / abs(avg_loss)
    return {
        "tradeCount": len(trades),
        "winRate": (len(wins) / len(trades)) if trades else None,
        "avgWin": avg_win,
        "avgLoss": avg_loss,
        "expectancy": _avg(pnls),
        "rewardRisk": reward_risk,
        "maxDrawdown": _max_drawdown(pnls) if trades else None,
        "costs": sum(trade["cost"] for trade in trades),
    }


def summarize_trades(trades):
    ordered = sorted(trades, key=lambda trade: (trade["signalDate"], trade["symbol"]))
    split = int(len(ordered) * 0.7)
    by_regime = {}
    by_rate = {}
    for trade in ordered:
        by_regime.setdefault(trade["regime"], []).append(trade)
        by_rate.setdefault(trade["rateCycle"], []).append(trade)
    summary = _bucket(ordered)
    summary.update(
        {
            "strategySystem": five.SYSTEM_NAME,
            "priceBasis": "next open entry; close for target and stop",
            "costsIncluded": True,
            "feeRate": five.FEE_RATE,
            "feeDiscount": five.FEE_DISCOUNT,
            "sellTaxRate": five.SELL_TAX_RATE,
            "inSample": _bucket(ordered[:split]),
            "outOfSample": _bucket(ordered[split:]),
            "byRegime": {name: _bucket(items) for name, items in sorted(by_regime.items())},
            "byRateCycle": {name: _bucket(items) for name, items in sorted(by_rate.items())} or {"未提供": _bucket([])},
            "survivorshipCaveat": SURVIVORSHIP_CAVEAT,
            "publishedWinRate": None,
            "note": "winRate 為 None 代表沒有成交，不是占位勝率。樣本結果不得寫進網站。",
        }
    )
    return {"summary": summary, "trades": ordered}


def load_yahoo(symbols, period="2y"):
    import yfinance as yf

    frames = {}
    downloaded = yf.download(
        " ".join(symbols),
        period=period,
        interval="1d",
        group_by="ticker",
        auto_adjust=False,
        progress=False,
        threads=False,
    )
    for symbol in symbols:
        if isinstance(getattr(downloaded, "columns", None), pd.MultiIndex) and symbol in downloaded.columns.get_level_values(0):
            frame = downloaded[symbol]
        else:
            frame = downloaded
        frame = five.flatten_price_columns(frame)
        if frame is None or "Close" not in getattr(frame, "columns", []):
            continue
        frame = frame.dropna(subset=["Close"])
        if not frame.empty:
            frames[symbol] = frame
    return frames


def main():
    parser = argparse.ArgumentParser(description="Backtest 阿波五燈交易系統 1.0. Does not publish a win rate.")
    parser.add_argument("--symbols", default="2330.TW,2317.TW,2454.TW", help="Comma-separated Yahoo symbols")
    parser.add_argument("--period", default="2y")
    parser.add_argument("--market", default="台股")
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    symbols = [item.strip() for item in args.symbols.split(",") if item.strip()]
    frames = load_yahoo(symbols, args.period)
    if not frames:
        raise SystemExit("沒有下載到任何日K，未產生勝率。")
    index_symbol = "^TWII" if args.market == "台股" else "^GSPC"
    index_frames = load_yahoo([index_symbol], args.period)
    result = run_backtest(frames, index_frames.get(index_symbol), market=args.market)
    result["summary"]["sampleSymbols"] = list(frames)
    result["summary"]["samplePeriod"] = args.period
    text = json.dumps(result["summary"], ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
