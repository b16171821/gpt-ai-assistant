import csv
import json
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import twstock
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
LATEST_JSON = DATA_DIR / "latest.json"
LATEST_CSV = DATA_DIR / "latest.csv"
HISTORY_CSV = DATA_DIR / "history.csv"
THEME_PATH = DATA_DIR / "theme_keywords.json"
TAIPEI_TZ = timezone(timedelta(hours=8))

MIN_RISE_60 = 30
MIN_VOLUME_RATIO = 2
MIN_ADX = 25
MIN_HISTORY_ROWS = 80
PERIOD = "1y"
BATCH_SIZE = 80
MAX_CODES = 1200


def now_tw():
    return datetime.now(TAIPEI_TZ).isoformat()


def safe_float(v, default=0.0):
    try:
        if pd.isna(v):
            return default
        return float(v)
    except Exception:
        return default


def safe_round(v, digits=2):
    v = safe_float(v)
    if math.isfinite(v):
        return round(v, digits)
    return 0


def load_theme_config():
    if THEME_PATH.exists():
        return json.loads(THEME_PATH.read_text(encoding="utf-8"))
    return {"keywords": [], "manual_themes": {}}


def get_stock_master():
    rows = []
    for code, item in twstock.codes.items():
        if not str(code).isdigit() or len(str(code)) != 4:
            continue
        market = getattr(item, "market", "") or ""
        type_ = getattr(item, "type", "") or ""
        name = getattr(item, "name", "") or ""
        if "股票" not in type_:
            continue
        suffix = ".TW" if "上市" in market else ".TWO"
        rows.append({"code": str(code), "name": name, "market": market, "ticker": f"{code}{suffix}"})
    rows = rows[:MAX_CODES]
    return rows


def calc_adx(df, period=14):
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    close = df["Close"].astype(float)
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    plus_di = 100 * plus_dm.rolling(period).mean() / atr
    minus_di = 100 * minus_dm.rolling(period).mean() / atr
    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)) * 100
    return dx.rolling(period).mean()


def kline_label(row):
    close = safe_float(row.get("Close"))
    open_ = safe_float(row.get("Open"))
    high = safe_float(row.get("High"))
    low = safe_float(row.get("Low"))
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


def analyze_one(df, meta, theme_config):
    if df is None or df.empty:
        return None
    df = df.dropna(subset=["Close"]).copy()
    if len(df) < MIN_HISTORY_ROWS:
        return None
    df["MA5"] = df["Close"].rolling(5).mean()
    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA60"] = df["Close"].rolling(60).mean()
    df["MA240"] = df["Close"].rolling(240).mean()
    df["AVG_VOLUME20"] = df["Volume"].rolling(20).mean()
    df["ADX"] = calc_adx(df)
    latest = df.iloc[-1]
    close_60 = safe_float(df.iloc[-60]["Close"])
    close = safe_float(latest["Close"])
    if close <= 0 or close_60 <= 0:
        return None
    rise60 = (close / close_60 - 1) * 100
    ma20 = safe_float(latest["MA20"])
    ma60 = safe_float(latest["MA60"])
    ma240 = safe_float(latest["MA240"])
    avg_volume20 = safe_float(latest["AVG_VOLUME20"])
    volume = safe_float(latest["Volume"])
    adx = safe_float(latest["ADX"])
    theme_text = theme_config.get("manual_themes", {}).get(meta["code"], "")
    keywords = theme_config.get("keywords", [])
    theme_hit = bool(theme_text) and any(k.lower() in theme_text.lower() for k in keywords)
    trend = close > ma20 and close > ma60 and (ma240 == 0 or close > ma240) and ma20 >= ma60
    fund = avg_volume20 > 0 and volume / avg_volume20 >= MIN_VOLUME_RATIO
    kline = kline_label(latest)
    price_vol = rise60 >= MIN_RISE_60 and adx >= MIN_ADX and "長黑" not in kline
    score = int(trend) + int(fund) + int(theme_hit) + int(price_vol)
    if score >= 4:
        status = "符合觀察：等回踩或確認，不追高"
    elif score == 3:
        status = "可列觀察：缺一燈，等補強"
    elif score <= 1:
        status = "避開：條件不足"
    else:
        status = "只觀察"
    reasons = []
    if trend:
        reasons.append("趨勢多頭")
    if fund:
        reasons.append("量能放大")
    if theme_hit:
        reasons.append("題材命中")
    if price_vol:
        reasons.append("價量條件足")
    return {
        "date": str(df.index[-1].date()),
        "market": meta["market"],
        "code": meta["code"],
        "name": meta["name"],
        "close": safe_round(close),
        "ma5": safe_round(latest["MA5"]),
        "ma20": safe_round(ma20),
        "ma60": safe_round(ma60),
        "ma240": safe_round(ma240),
        "rise60": safe_round(rise60),
        "volume": int(volume),
        "avgVolume20": int(avg_volume20),
        "adx": safe_round(adx),
        "kline": kline,
        "theme": theme_text,
        "trend": bool(trend),
        "fund": bool(fund),
        "themeHit": bool(theme_hit),
        "priceVol": bool(price_vol),
        "score": int(score),
        "status": status,
        "reason": "、".join(reasons) if reasons else "條件不足",
    }


def scan_market():
    stock_master = get_stock_master()
    theme_config = load_theme_config()
    by_ticker = {x["ticker"]: x for x in stock_master}
    results = []
    errors = []
    tickers = list(by_ticker.keys())

    for i in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[i:i + BATCH_SIZE]
        try:
            data = yf.download(
                tickers=" ".join(batch),
                period=PERIOD,
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                progress=False,
                threads=True,
            )
        except Exception as e:
            errors.append(f"batch {i}-{i + len(batch)} failed: {e}")
            continue

        for ticker in batch:
            try:
                if len(batch) == 1:
                    df = data
                else:
                    if ticker not in data.columns.get_level_values(0):
                        continue
                    df = data[ticker]
                row = analyze_one(df, by_ticker[ticker], theme_config)
                if row:
                    results.append(row)
            except Exception as e:
                errors.append(f"{ticker} failed: {e}")

    results = sorted(results, key=lambda x: (x["score"], x["rise60"], x["volume"]), reverse=True)
    return results, errors, len(stock_master)


def write_outputs(rows, errors, total_master):
    DATA_DIR.mkdir(exist_ok=True)
    qualified = [r for r in rows if r["score"] >= 3]
    payload = {
        "meta": {
            "updatedAt": now_tw(),
            "mode": "real-market-scan",
            "totalMaster": total_master,
            "totalAnalyzed": len(rows),
            "qualified3Plus": len(qualified),
            "qualified4": sum(1 for r in rows if r["score"] >= 4),
            "errors": errors[:50],
            "note": "資料源：twstock 股票清單 + yfinance 日K。輸出為觀察清單，不構成買賣建議。"
        },
        "stocks": rows
    }
    LATEST_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    fieldnames = list(rows[0].keys()) if rows else ["date", "code", "name", "score", "status", "reason"]
    with LATEST_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    with HISTORY_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_failure(e):
    DATA_DIR.mkdir(exist_ok=True)
    payload = {
        "meta": {
            "updatedAt": now_tw(),
            "mode": "failed-safe",
            "error": str(e),
            "note": "Scanner failed but workflow stayed green so the error can be inspected in latest.json."
        },
        "stocks": []
    }
    LATEST_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    LATEST_CSV.write_text("date,code,name,score,status,reason\n", encoding="utf-8-sig")
    HISTORY_CSV.write_text("date,code,name,score,status,reason\n", encoding="utf-8-sig")
    print(f"FAILED-SAFE: {e}")


def main():
    try:
        rows, errors, total_master = scan_market()
        write_outputs(rows, errors, total_master)
        print(f"Scan complete. analyzed={len(rows)} errors={len(errors)}")
    except Exception as e:
        write_failure(e)


if __name__ == "__main__":
    main()
