import csv
import json
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

import apo_five_lights as five
from market_filter import apply_breadth_guard, apply_market_filter_to_rows, classify_market_regime, previous_a_count_from_snapshots

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
WATCHLIST_PATH = DATA_DIR / "us_watchlist.json"
LATEST_JSON = DATA_DIR / "us_latest.json"
LATEST_CSV = DATA_DIR / "us_latest.csv"
REPORT_MD = DATA_DIR / "us_latest_report.md"
SNAPSHOTS_PATH = DATA_DIR / "strategy_snapshots.json"
TAIPEI_TZ = ZoneInfo("Asia/Taipei")
EASTERN_TZ = ZoneInfo("America/New_York")

MIN_RISE_60 = five.STRONG_STOCK_RISE60
MIN_VOLUME_RATIO = five.VOLUME_BREAKOUT_MULTIPLE
MIN_ADX = five.ADX_MIN
MIN_HISTORY_ROWS = five.MIN_BARS
PERIOD = "3y"
BATCH_SIZE = 40
US_CLOSE_CONFIRM_MINUTE = 16 * 60 + 15
DOWNLOAD_LOOKBACK_DAYS = 900

INDEX_TICKERS = {"SPX": "^GSPC", "NASDAQ": "^IXIC", "DJI": "^DJI"}


def now_et():
    return datetime.now(EASTERN_TZ)


def now_tw():
    return datetime.now(TAIPEI_TZ)


def market_session_status(dt=None):
    dt = dt or now_et()
    if dt.weekday() >= 5:
        return "休市"
    minutes = dt.hour * 60 + dt.minute
    if minutes < 4 * 60:
        return "盤後"
    if 4 * 60 <= minutes < 9 * 60 + 30:
        return "盤前"
    if 9 * 60 + 30 <= minutes < 16 * 60:
        return "盤中"
    if 16 * 60 <= minutes < 20 * 60:
        return "盤後"
    return "收盤後"


def trim_to_completed_daily_bars(df):
    """只保留完整收盤日K；若 yfinance 抓到美股當日未完成日K，直接移除。"""
    if df is None or df.empty:
        return df
    x = df.dropna(subset=["Close"]).copy()
    if x.empty:
        return x
    x.index = pd.to_datetime(x.index)
    now = now_et()
    minutes = now.hour * 60 + now.minute
    today = now.date()
    latest_date = x.index[-1].date()
    if latest_date >= today and minutes < US_CLOSE_CONFIRM_MINUTE:
        x = x[x.index.date < today]
    return x.dropna(subset=["Close"])


def latest_completed_market_date(index_data):
    dates = []
    for df in index_data.values():
        x = trim_to_completed_daily_bars(df)
        if x is not None and not x.empty:
            dates.append(str(x.index[-1].date()))
    if not dates:
        raise RuntimeError("美股指數沒有可用的完整收盤資料")
    counts = {date: dates.count(date) for date in set(dates)}
    market_date = max(counts, key=lambda date: (counts[date], date))
    if counts[market_date] < 2:
        raise RuntimeError(f"美股指數收盤日期不一致：{dates}")
    return market_date


def sf(v, default=0.0):
    try:
        if pd.isna(v):
            return default
        return float(v)
    except Exception:
        return default


def sr(v, digits=2):
    v = sf(v)
    return round(v, digits) if math.isfinite(v) else 0


def load_watchlist():
    if WATCHLIST_PATH.exists():
        payload = json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))
        return [x.strip().upper() for x in payload.get("tickers", []) if x.strip()]
    return ["AAPL", "MSFT", "NVDA", "AMZN", "META", "TSLA", "AVGO", "AMD", "PLTR", "SMCI", "SPY", "QQQ", "DIA"]


def calc_adx(df, period=14):
    return five.wilder_dmi(df, period)["ADX"]


def kline_label(row):
    close, open_, high, low = sf(row.get("Close")), sf(row.get("Open")), sf(row.get("High")), sf(row.get("Low"))
    if not all([close, open_, high, low]) or high == low:
        return "資料不足"
    body = abs(close - open_)
    upper = high - max(close, open_)
    lower = min(close, open_) - low
    if close < open_ and body / close >= 0.04:
        return "長黑"
    if close > open_ and upper <= max(body * 0.8, close * 0.01):
        return "紅K突破"
    if lower >= max(body * 1.5, close * 0.015):
        return "下影線"
    return "一般K"


def pattern_plan(df):
    row = five.analyze_ohlcv(df, market="美股", symbol="", name="")
    return {} if not row else row


def analyze_one(df, ticker, name=None, market_ok=True):
    if df is None or df.empty:
        return None
    df = five.flatten_price_columns(trim_to_completed_daily_bars(df))
    if df is None or df.empty:
        return None
    row = five.analyze_ohlcv(
        df,
        market="美股",
        symbol=ticker,
        name=name or ticker,
        lot_size=five.US_LOT_SIZE,
        min_avg_turnover=five.US_MIN_AVG_TURNOVER20,
    )
    if not row:
        return None
    row["ticker"] = ticker
    row["currency"] = "USD"
    row["source"] = "Yahoo Finance via yfinance（日K完整收盤資料）"
    row["dataPolicy"] = "只使用完整收盤日K，不使用盤中未完成K。頸線與均量都不含當日。"
    row["kline"] = kline_label(df.iloc[-1])
    row["marketOk"] = bool(market_ok)
    if not market_ok and row.get("rawRating") == "待執行名單":
        row["rawRating"] = "高優先觀察"
        row["rating"] = "高優先觀察"
        row["grade"] = "高優先觀察"
        row["strictOk"] = False
        row["action"] = "個股五燈通過，但美股指數未同步轉強，降為高優先觀察。"
        row["planStatus"] = row["action"]
        row["status"] = "高優先觀察"
    row["trend"] = bool(row["lights"][0]["passed"])
    row["volumeOk"] = bool(row["lights"][2]["passed"])
    row["priceVol"] = bool(row["lights"][1]["passed"] and row["lights"][3]["passed"])
    return row


def market_analysis(index_data):
    rows = {}
    ok_count = 0
    for label, df in index_data.items():
        if df is None or df.empty:
            rows[label] = {"status": "資料不足", "close": 0, "ma20": 0, "trend": False}
            continue
        x = trim_to_completed_daily_bars(df)
        if x is None or x.empty or len(x.dropna(subset=["Close"])) < 80:
            rows[label] = {"status": "資料不足", "close": 0, "ma20": 0, "trend": False}
            continue
        x = x.dropna(subset=["Close"]).copy()
        x["MA20"] = x["Close"].rolling(20).mean()
        x["MA60"] = x["Close"].rolling(60).mean()
        latest = x.iloc[-1]
        close, ma20, ma60 = sf(latest["Close"]), sf(latest["MA20"]), sf(latest["MA60"])
        trend = close > ma20 and ma20 >= ma60
        if trend:
            ok_count += 1
        rows[label] = {"status": "站上月線" if close > ma20 else "跌破月線", "close": sr(close), "ma20": sr(ma20), "ma60": sr(ma60), "trend": bool(trend), "strategyAsOfDate": str(x.index[-1].date())}
    if ok_count >= 2:
        market_state, attack, suggestion = "多頭", "適合進攻", "積極"
    elif ok_count == 1:
        market_state, attack, suggestion = "震盪", "只適合精選", "保守"
    else:
        market_state, attack, suggestion = "空頭", "不適合進攻", "等待"
    regime = classify_market_regime(index_data.get("SPX"), index_data.get("NASDAQ"), support=0)
    return {"indexes": rows, "marketState": market_state, "attack": attack, "suggestion": suggestion, "marketOk": ok_count >= 2, "marketFilter": regime, "marketRegime": regime.get("marketRegime"), "regimeLabel": regime.get("regimeLabel"), "strategy": regime.get("strategy"), "warnings": regime.get("warnings", [])}


def download_group(tickers):
    end = now_et().date() + timedelta(days=1)
    start = end - timedelta(days=DOWNLOAD_LOOKBACK_DAYS)
    return yf.download(
        " ".join(tickers),
        start=start.isoformat(),
        end=end.isoformat(),
        interval="1d",
        group_by="ticker",
        auto_adjust=False,
        progress=False,
        threads=True,
    )


def scan_market():
    tickers = load_watchlist()
    errors = []
    index_raw = download_group(list(INDEX_TICKERS.values()))
    index_data = {}
    for label, ticker in INDEX_TICKERS.items():
        try:
            index_data[label] = index_raw[ticker]
        except Exception:
            index_data[label] = pd.DataFrame()
    market_date = latest_completed_market_date(index_data)
    market = market_analysis(index_data)
    results = []
    for i in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[i:i + BATCH_SIZE]
        try:
            data = download_group(batch)
        except Exception as e:
            errors.append(f"batch {i}-{i + len(batch)} failed: {e}")
            continue
        for ticker in batch:
            if ticker in ["SPY", "QQQ", "DIA", "IWM"]:
                continue
            try:
                df = data if len(batch) == 1 else data[ticker] if ticker in data.columns.get_level_values(0) else None
                row = analyze_one(df, ticker, ticker, market.get("marketOk", False))
                if row and row.get("strategyAsOfDate") == market_date:
                    row["dataDate"] = market_date
                    row["dataFresh"] = True
                    row["priceVerified"] = True
                    results.append(row)
                elif row:
                    errors.append(f"{ticker} skipped: 收盤日 {row.get('strategyAsOfDate')} 不等於市場完整收盤日 {market_date}")
            except Exception as e:
                errors.append(f"{ticker} failed: {e}")
    results = sorted(
        results,
        key=lambda x: (
            five.RATING_RANK.get(x.get("rawRating"), 0),
            x.get("lightsPassed", 0),
            x.get("strictOk", False),
            x.get("riskRewardRatio") or 0,
            -max(x.get("distanceFromNecklinePct") or 0, 0),
            x.get("volume") or 0,
        ),
        reverse=True,
    )
    market["marketFilter"] = apply_breadth_guard(market.get("marketFilter", {}), results, previous_a_count_from_snapshots(SNAPSHOTS_PATH, "美股"))
    market["marketRegime"] = market["marketFilter"].get("marketRegime")
    market["regimeLabel"] = market["marketFilter"].get("regimeLabel")
    market["strategy"] = market["marketFilter"].get("strategy")
    market["warnings"] = market["marketFilter"].get("warnings", [])
    results = apply_market_filter_to_rows(results, market.get("marketFilter", {}))
    for row in results:
        regime = row.get("marketRegime")
        if not regime:
            continue
        gated = five.gate_rating(row.get("rawRating"), regime)
        row["rating"] = gated
        row["grade"] = gated
        allows = regime == "ATTACK" and row.get("rawRating") == "待執行名單"
        row["marketFilterAllowsEntry"] = bool(allows)
        if not allows:
            row["strictOk"] = False
    if not results:
        raise RuntimeError(f"美股沒有符合完整收盤日 {market_date} 的有效資料")
    return results, errors, market, len(tickers), market_date


def card(row, idx):
    action = "可研究進場" if row.get("strictOk") else "先觀察，等確認"
    if row.get("forbiddenChase"):
        action = "禁止追高"
    return f"""
## {idx}. {row.get('name')}（{row.get('ticker')}）｜{row.get('rating')}｜{row.get('lightsPassed')}/5燈

**一句話策略：** {action}。{row.get('planStatus')}

**資料時間**
- 策略依據：{row.get('strategyAsOfDate')} 收盤資料
- 價格類型：{row.get('priceType')}
- 資料來源：{row.get('source')}

**型態與原因**
- 型態：{row.get('pattern')}｜階段：{row.get('stage')}
- 原因：{row.get('reason')}
- K棒：{row.get('kline')}｜ADX：{row.get('adx')}｜60日漲幅：{row.get('rise60')}%

**關鍵價位**
- 頸線 / 壓力：{row.get('neckline')}
- 支撐：{row.get('support')}
- 滿足點：{row.get('target')}
- 停損：{row.get('stopLoss')}

**進場計畫**
- 突破進場：{row.get('entryBreakout')}
- 回踩進場：{row.get('entryPullback')}
- 合理追價範圍：{row.get('chaseRangeLow')} ～ {row.get('chaseRangeHigh')}
- 距離頸線：{row.get('distanceFromNecklinePct')}%｜風險：約 {row.get('riskPct')}%

**確認條件**
{row.get('confirmConditions')}
"""


def write_report(rows, meta, market):
    strict = [r for r in rows if r.get("strictOk")]
    watch = [r for r in rows if r.get("score", 0) >= 3 and not r.get("strictOk")]
    top = (strict + watch)[:3]
    conclusion = "有五燈待執行名單，仍須大盤允許進場。" if strict else ("今日沒有五燈正式訊號；以下為觀察名單。" if watch else "今日沒有五燈正式訊號。")
    lines = [f"# {five.SYSTEM_LABEL}美股報告", "", "勝率：尚未回測。本報告不顯示未經驗證的勝率。", "", f"策略依據：{meta.get('strategyAsOfDate')} 收盤資料", f"系統更新（美東）：{meta.get('updatedAtET')}", f"系統更新（台灣）：{meta.get('updatedAtTW')}", f"市場狀態：{meta.get('sessionStatus')}", "", f"## 今日大盤狀態：{market.get('marketRegime')} {market.get('regimeLabel')}", f"今日策略：{market.get('strategy')}", "", f"## 結論：{conclusion}", "", "## 美股大盤判斷", f"- S&P500：{market['indexes'].get('SPX', {}).get('status')}｜收盤 {market['indexes'].get('SPX', {}).get('close')}｜20MA {market['indexes'].get('SPX', {}).get('ma20')}", f"- NASDAQ：{market['indexes'].get('NASDAQ', {}).get('status')}｜收盤 {market['indexes'].get('NASDAQ', {}).get('close')}｜20MA {market['indexes'].get('NASDAQ', {}).get('ma20')}", f"- 道瓊：{market['indexes'].get('DJI', {}).get('status')}｜收盤 {market['indexes'].get('DJI', {}).get('close')}｜20MA {market['indexes'].get('DJI', {}).get('ma20')}", f"- 當前市場：{market.get('marketState')}", f"- 是否適合進攻：{market.get('attack')}", f"- 建議：{market.get('suggestion')}", "", "## 判讀順序", "1. 本報告只使用完整收盤日K，不使用盤中未完成K。", "2. 頸線與 20 日均量都不含當日。", "3. 先看大盤濾網：只有進攻盤能保留待執行名單。", "4. 五燈才是正式訊號；跌破與延伸分開看。", "", "---"]
    if not top:
        lines.append("今日沒有五燈正式訊號。")
    else:
        for i, r in enumerate(top, 1):
            lines.append(card(r, i))
            lines.append("---")
    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")


def write_outputs(rows, errors, market, total_watchlist, market_date):
    DATA_DIR.mkdir(exist_ok=True)
    strict = [r for r in rows if r.get("strictOk")]
    qualified = [r for r in rows if r.get("score", 0) >= 3]
    row_dates = {r.get("strategyAsOfDate") for r in rows}
    if row_dates != {market_date}:
        raise RuntimeError(f"美股策略日期不一致：{sorted(row_dates)}，預期 {market_date}")
    meta = {"updatedAtET": now_et().strftime("%Y/%m/%d %H:%M:%S %Z"), "updatedAtTW": now_tw().strftime("%Y/%m/%d %H:%M:%S %Z"), "strategyAsOfDate": market_date, "completedDataDate": market_date, "strategySystem": five.SYSTEM_NAME, "strategyLabel": five.SYSTEM_LABEL, "backtestStatus": "尚未回測", "freshnessStatus": "COMPLETE", "freshCount": len(rows), "staleCount": 0, "priceType": "收盤確認價", "dataPolicy": "只使用完整收盤日K。指標用還原價，買賣價用原始收盤。均量與頸線不含當日。", "sessionStatus": market_session_status(), "mode": "apo-five-lights-1.0", "source": "Yahoo Finance via yfinance", "totalWatchlist": total_watchlist, "totalAnalyzed": len(rows), "qualified3Plus": len(qualified), "strictCandidates": len(strict), "ratingReady": sum(1 for r in rows if r.get("rawRating") == "待執行名單"), "ratingHigh": sum(1 for r in rows if r.get("rawRating") == "高優先觀察"), "ratingWatch": sum(1 for r in rows if r.get("rawRating") == "觀察"), "extendedAboveCount": sum(1 for r in rows if r.get("extendedAbove")), "brokeBelowCount": sum(1 for r in rows if r.get("brokeBelow")), "marketRegime": market.get("marketRegime"), "marketRegimeLabel": market.get("regimeLabel"), "marketStrategy": market.get("strategy"), "marketFilter": market.get("marketFilter"), "errors": errors[:50]}
    payload = {"meta": meta, "market": market, "stocks": rows}
    LATEST_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    flat_rows = [
        {key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value for key, value in row.items()}
        for row in rows
    ]
    fieldnames = list(flat_rows[0].keys()) if flat_rows else ["date", "ticker", "name", "score", "status", "reason"]
    with LATEST_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(flat_rows)
    write_report(rows, meta, market)


def write_failure(e):
    DATA_DIR.mkdir(exist_ok=True)
    payload = {"meta": {"updatedAtET": now_et().isoformat(), "updatedAtTW": now_tw().isoformat(), "mode": "failed-safe", "error": str(e)}, "stocks": []}
    LATEST_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    LATEST_CSV.write_text("date,ticker,name,score,status,reason\n", encoding="utf-8-sig")
    REPORT_MD.write_text(f"# {five.SYSTEM_LABEL}美股報告\n\n流程失敗：{e}\n勝率：尚未回測。\n", encoding="utf-8")
    print(f"FAILED-SAFE: {e}")


def main():
    rows, errors, market, total_watchlist, market_date = scan_market()
    write_outputs(rows, errors, market, total_watchlist, market_date)
    print(f"US scan complete. date={market_date} analyzed={len(rows)} errors={len(errors)}")


if __name__ == "__main__":
    main()

