import csv
import json
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
WATCHLIST_PATH = DATA_DIR / "us_watchlist.json"
LATEST_JSON = DATA_DIR / "us_latest.json"
LATEST_CSV = DATA_DIR / "us_latest.csv"
REPORT_MD = DATA_DIR / "us_latest_report.md"
TAIPEI_TZ = ZoneInfo("Asia/Taipei")
EASTERN_TZ = ZoneInfo("America/New_York")

MIN_RISE_60 = 30
MIN_VOLUME_RATIO = 2
MIN_ADX = 25
MIN_HISTORY_ROWS = 220
PERIOD = "1y"
BATCH_SIZE = 40
US_CLOSE_CONFIRM_MINUTE = 16 * 60 + 15

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
    high, low, close = df["High"].astype(float), df["Low"].astype(float), df["Close"].astype(float)
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    plus_di = 100 * plus_dm.rolling(period).mean() / atr
    minus_di = 100 * minus_dm.rolling(period).mean() / atr
    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)) * 100
    return dx.rolling(period).mean()


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
    r = df.tail(60).copy()
    close = sf(r.iloc[-1]["Close"])
    if close <= 0 or len(r) < 40:
        return {}
    high20 = sf(r["High"].tail(20).max())
    high60 = sf(r["High"].max())
    low20 = sf(r["Low"].tail(20).min())
    low60 = sf(r["Low"].min())
    avg20v = sf(r["Volume"].tail(20).mean())
    volume = sf(r.iloc[-1]["Volume"])
    vol_ratio = volume / avg20v if avg20v else 0
    neckline = high20
    support = max(low20, sf(r["Close"].tail(20).mean()) * 0.95)
    compression = (high20 - low20) / close * 100 if close else 999
    distance_from_neckline = (close - neckline) / neckline * 100 if neckline else 999
    distance_from_prev_high = abs(close - high60) / high60 * 100 if high60 else 999
    upper_shadow_pct = (sf(r.iloc[-1]["High"]) - max(close, sf(r.iloc[-1]["Open"]))) / close * 100 if close else 999
    if compression <= 12 and close >= neckline * 0.98:
        pattern = "箱型突破"
    elif compression <= 18 and vol_ratio >= 1.5:
        pattern = "三角收斂突破"
    elif close > sf(r["Close"].tail(10).mean()) and low20 > low60 * 1.05:
        pattern = "旗型整理突破"
    else:
        pattern = "接近成形"
    breakout_confirmed = close >= neckline * 1.02 and vol_ratio >= 2 and upper_shadow_pct <= 3
    pullback_confirmed = abs(close - neckline) / neckline * 100 <= 3 and close >= neckline and vol_ratio >= 1
    stage = "突破第一根" if breakout_confirmed else ("回踩頸線不破" if pullback_confirmed else "接近成形＋可觀察")
    target = neckline + (neckline - low60)
    entry_breakout = neckline * 1.02
    entry_pullback = neckline
    chase_low = entry_breakout
    chase_high = entry_breakout * 1.03
    stop_loss = neckline * 0.97
    risk_pct = (close - stop_loss) / close * 100 if close else 999
    forbidden = distance_from_neckline > 5 or distance_from_prev_high > 10
    if breakout_confirmed and not forbidden:
        win_rate = "高"
        plan_status = "突破確認，可在追價範圍內掛單"
    elif pullback_confirmed and not forbidden:
        win_rate = "中"
        plan_status = "回踩頸線不破，可觀察低風險進場"
    elif forbidden:
        win_rate = "低"
        plan_status = "距離頸線或前高過遠，禁止追高"
    else:
        win_rate = "中"
        plan_status = "預備單，等待確認條件"
    confirm = f"收盤站上頸線 {sr(neckline)} 至少2%；成交量>=20日均量2倍；不可長上影；隔日不跌破頸線；距離頸線>5%禁止進場"
    return {"pattern": pattern, "neckline": sr(neckline), "support": sr(support), "target": sr(target), "stage": stage, "entryBreakout": sr(entry_breakout), "entryPullback": sr(entry_pullback), "chaseRangeLow": sr(chase_low), "chaseRangeHigh": sr(chase_high), "stopLoss": sr(stop_loss), "riskPct": sr(risk_pct), "winRate": win_rate, "distanceFromNecklinePct": sr(distance_from_neckline), "distanceFromPrevHighPct": sr(distance_from_prev_high), "forbiddenChase": bool(forbidden), "planStatus": plan_status, "confirmConditions": confirm}


def analyze_one(df, ticker, name=None, market_ok=True):
    if df is None or df.empty:
        return None
    df = trim_to_completed_daily_bars(df)
    if df is None or df.empty:
        return None
    if len(df) < MIN_HISTORY_ROWS:
        return None
    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA60"] = df["Close"].rolling(60).mean()
    df["MA200"] = df["Close"].rolling(200).mean()
    df["AVG_VOLUME20"] = df["Volume"].rolling(20).mean()
    df["ADX"] = calc_adx(df)
    latest = df.iloc[-1]
    close_60 = sf(df.iloc[-60]["Close"])
    close = sf(latest["Close"])
    if close <= 0 or close_60 <= 0:
        return None
    rise60 = (close / close_60 - 1) * 100
    ma20, ma60, ma200 = sf(latest["MA20"]), sf(latest["MA60"]), sf(latest["MA200"])
    avg_volume20, volume, adx = sf(latest["AVG_VOLUME20"]), sf(latest["Volume"]), sf(latest["ADX"])
    trend = close > ma20 and close > ma60 and close > ma200 and ma20 > ma60 > ma200
    volume_ok = avg_volume20 > 0 and volume / avg_volume20 >= MIN_VOLUME_RATIO
    kline = kline_label(latest)
    price_vol = rise60 >= MIN_RISE_60 and adx >= MIN_ADX and "長黑" not in kline
    score = int(trend) + int(volume_ok) + int(price_vol) + int(market_ok)
    plan = pattern_plan(df)
    strict_ok = score >= 4 and not plan.get("forbiddenChase", True) and plan.get("stage") in ["突破第一根", "回踩頸線不破"]
    status = "高勝率候選：四條件＋型態確認" if strict_ok else ("接近成形＋可觀察進場" if score >= 3 else "只觀察")
    reasons = []
    if trend: reasons.append("均線多頭排列")
    if volume_ok: reasons.append("量能放大")
    if price_vol: reasons.append("價量趨勢成立")
    if market_ok: reasons.append("大盤不弱")
    strategy_date = str(df.index[-1].date())
    base = {"date": strategy_date, "strategyAsOfDate": strategy_date, "ticker": ticker, "name": name or ticker, "close": sr(close), "currency": "USD", "priceType": "收盤確認價", "source": "Yahoo Finance via yfinance（日K完整收盤資料）", "dataPolicy": "只使用完整收盤日K，不使用盤中未完成K", "ma20": sr(ma20), "ma60": sr(ma60), "ma200": sr(ma200), "rise60": sr(rise60), "volume": int(volume), "avgVolume20": int(avg_volume20), "volumeRatio": sr(volume / avg_volume20 if avg_volume20 else 0), "adx": sr(adx), "kline": kline, "trend": bool(trend), "volumeOk": bool(volume_ok), "priceVol": bool(price_vol), "marketOk": bool(market_ok), "score": int(score), "strictOk": bool(strict_ok), "status": status, "reason": "、".join(reasons) if reasons else "條件不足"}
    base.update(plan)
    return base


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
    return {"indexes": rows, "marketState": market_state, "attack": attack, "suggestion": suggestion, "marketOk": ok_count >= 2}


def download_group(tickers):
    return yf.download(" ".join(tickers), period=PERIOD, interval="1d", group_by="ticker", auto_adjust=False, progress=False, threads=True)


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
                if row:
                    results.append(row)
            except Exception as e:
                errors.append(f"{ticker} failed: {e}")
    results = sorted(results, key=lambda x: (x.get("strictOk", False), x["score"], x.get("winRate") == "高", x["rise60"], x["volume"]), reverse=True)
    return results, errors, market, len(tickers)


def card(row, idx):
    action = "可研究進場" if row.get("strictOk") else "先觀察，等確認"
    if row.get("forbiddenChase"):
        action = "禁止追高"
    return f"""
## {idx}. {row.get('name')}（{row.get('ticker')}）｜{row.get('score')}/4｜{row.get('winRate')}勝率

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
    conclusion = "有符合嚴格條件的美股候選，但仍要等隔日不跌破頸線確認。" if strict else ("今日無完整高勝率標的；以下為接近成形＋可觀察進場3檔。" if watch else "今日無符合型態學高勝率標的。")
    lines = ["# 美股型態學高勝率策略報告", "", f"策略依據：{meta.get('strategyAsOfDate')} 收盤資料", f"系統更新（美東）：{meta.get('updatedAtET')}", f"系統更新（台灣）：{meta.get('updatedAtTW')}", f"市場狀態：{meta.get('sessionStatus')}", "", f"## 結論：{conclusion}", "", "## 美股大盤判斷", f"- S&P500：{market['indexes'].get('SPX', {}).get('status')}｜收盤 {market['indexes'].get('SPX', {}).get('close')}｜20MA {market['indexes'].get('SPX', {}).get('ma20')}", f"- NASDAQ：{market['indexes'].get('NASDAQ', {}).get('status')}｜收盤 {market['indexes'].get('NASDAQ', {}).get('close')}｜20MA {market['indexes'].get('NASDAQ', {}).get('ma20')}", f"- 道瓊：{market['indexes'].get('DJI', {}).get('status')}｜收盤 {market['indexes'].get('DJI', {}).get('close')}｜20MA {market['indexes'].get('DJI', {}).get('ma20')}", f"- 當前市場：{market.get('marketState')}", f"- 是否適合進攻：{market.get('attack')}", f"- 建議：{market.get('suggestion')}", "", "## 判讀順序", "1. 本報告只使用完整收盤日K，不使用盤中未完成K。", "2. 先看結論：有沒有嚴格候選。", "3. 再看一句話策略：可進場 / 禁止追高 / 等確認。", "4. 最後看進場點、停損、追價範圍。", "", "---"]
    if not top:
        lines.append("今日無符合型態學高勝率標的。")
    else:
        for i, r in enumerate(top, 1):
            lines.append(card(r, i))
            lines.append("---")
    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")


def write_outputs(rows, errors, market, total_watchlist):
    DATA_DIR.mkdir(exist_ok=True)
    strict = [r for r in rows if r.get("strictOk")]
    qualified = [r for r in rows if r.get("score", 0) >= 3]
    strategy_date = max([r.get("strategyAsOfDate", "") for r in rows], default="")
    meta = {"updatedAtET": now_et().strftime("%Y/%m/%d %H:%M:%S %Z"), "updatedAtTW": now_tw().strftime("%Y/%m/%d %H:%M:%S %Z"), "strategyAsOfDate": strategy_date, "priceType": "收盤確認價", "dataPolicy": "只使用完整收盤日K，不使用盤中未完成K", "sessionStatus": market_session_status(), "mode": "us-pattern-close-only-scan", "source": "Yahoo Finance via yfinance", "totalWatchlist": total_watchlist, "totalAnalyzed": len(rows), "qualified3Plus": len(qualified), "strictCandidates": len(strict), "errors": errors[:50]}
    payload = {"meta": meta, "market": market, "stocks": rows}
    LATEST_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    fieldnames = list(rows[0].keys()) if rows else ["date", "ticker", "name", "score", "status", "reason"]
    with LATEST_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    write_report(rows, meta, market)


def write_failure(e):
    DATA_DIR.mkdir(exist_ok=True)
    payload = {"meta": {"updatedAtET": now_et().isoformat(), "updatedAtTW": now_tw().isoformat(), "mode": "failed-safe", "error": str(e)}, "stocks": []}
    LATEST_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    LATEST_CSV.write_text("date,ticker,name,score,status,reason\n", encoding="utf-8-sig")
    REPORT_MD.write_text(f"# 美股型態學高勝率策略報告\n\n流程失敗：{e}\n", encoding="utf-8")
    print(f"FAILED-SAFE: {e}")


def main():
    try:
        rows, errors, market, total_watchlist = scan_market()
        write_outputs(rows, errors, market, total_watchlist)
        print(f"US scan complete. analyzed={len(rows)} errors={len(errors)}")
    except Exception as e:
        write_failure(e)


if __name__ == "__main__":
    main()
