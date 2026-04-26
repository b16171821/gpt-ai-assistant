import csv
import io
import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
HISTORY_PATH = DATA_DIR / "history.csv"
LATEST_JSON = DATA_DIR / "latest.json"
LATEST_CSV = DATA_DIR / "latest.csv"
THEME_PATH = DATA_DIR / "theme_keywords.json"
TAIPEI_TZ = timezone(timedelta(hours=8))

USER_AGENT = "Mozilla/5.0 four-lights-scanner/1.0"


def today_taipei() -> datetime:
    return datetime.now(TAIPEI_TZ)


def roc_or_number_to_float(value):
    if value is None:
        return np.nan
    s = str(value).replace(",", "").replace("--", "").replace("X", "").strip()
    if s in {"", "-", "nan", "None"}:
        return np.nan
    try:
        return float(s)
    except ValueError:
        return np.nan


def load_theme_config():
    if not THEME_PATH.exists():
        return {"keywords": [], "manual_themes": {}}
    return json.loads(THEME_PATH.read_text(encoding="utf-8"))


def fetch_twse_daily(date: datetime) -> pd.DataFrame:
    date_str = date.strftime("%Y%m%d")
    url = "https://www.twse.com.tw/exchangeReport/MI_INDEX"
    params = {"response": "json", "date": date_str, "type": "ALLBUT0999"}
    r = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=30)
    r.raise_for_status()
    payload = r.json()
    tables = payload.get("tables", [])
    target = None
    for table in tables:
        fields = table.get("fields", [])
        if "證券代號" in fields and "收盤價" in fields:
            target = table
            break
    if not target:
        return pd.DataFrame()
    rows = []
    fields = target["fields"]
    for raw in target.get("data", []):
        row = dict(zip(fields, raw))
        code = str(row.get("證券代號", "")).strip()
        if not code.isdigit():
            continue
        rows.append({
            "date": date.strftime("%Y-%m-%d"),
            "market": "TWSE",
            "code": code,
            "name": str(row.get("證券名稱", "")).strip(),
            "close": roc_or_number_to_float(row.get("收盤價")),
            "high": roc_or_number_to_float(row.get("最高價")),
            "low": roc_or_number_to_float(row.get("最低價")),
            "open": roc_or_number_to_float(row.get("開盤價")),
            "volume": roc_or_number_to_float(row.get("成交股數")),
        })
    return pd.DataFrame(rows)


def fetch_tpex_daily(date: datetime) -> pd.DataFrame:
    # TPEx API may change. This endpoint is the common daily close quotation JSON.
    roc_year = date.year - 1911
    date_str = f"{roc_year}/{date.month:02d}/{date.day:02d}"
    url = "https://www.tpex.org.tw/www/zh-tw/afterTrading/otc"
    params = {"date": date_str, "type": "EW", "response": "json"}
    try:
        r = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=30)
        r.raise_for_status()
        payload = r.json()
    except Exception:
        return pd.DataFrame()

    data = payload.get("tables", [{}])[0].get("data", []) if isinstance(payload.get("tables"), list) else payload.get("data", [])
    fields = payload.get("tables", [{}])[0].get("fields", []) if isinstance(payload.get("tables"), list) else payload.get("fields", [])
    rows = []
    for raw in data:
        if isinstance(raw, dict):
            row = raw
        else:
            row = dict(zip(fields, raw))
        code = str(row.get("代號") or row.get("證券代號") or row.get("股票代號") or "").strip()
        if not code.isdigit():
            continue
        rows.append({
            "date": date.strftime("%Y-%m-%d"),
            "market": "TPEx",
            "code": code,
            "name": str(row.get("名稱") or row.get("證券名稱") or row.get("股票名稱") or "").strip(),
            "close": roc_or_number_to_float(row.get("收盤") or row.get("收盤價")),
            "high": roc_or_number_to_float(row.get("最高") or row.get("最高價")),
            "low": roc_or_number_to_float(row.get("最低") or row.get("最低價")),
            "open": roc_or_number_to_float(row.get("開盤") or row.get("開盤價")),
            "volume": roc_or_number_to_float(row.get("成交股數") or row.get("成交股數(股)") or row.get("成交量")),
        })
    return pd.DataFrame(rows)


def fetch_recent_trading_day(max_lookback=10) -> pd.DataFrame:
    now = today_taipei()
    frames = []
    picked_date = None
    for i in range(max_lookback):
        d = now - timedelta(days=i)
        twse = fetch_twse_daily(d)
        tpex = fetch_tpex_daily(d)
        frames = [df for df in [twse, tpex] if not df.empty]
        if frames:
            picked_date = d
            break
    if not frames:
        raise RuntimeError("找不到最近交易日資料，可能是資料源暫時失效。")
    daily = pd.concat(frames, ignore_index=True)
    daily = daily.dropna(subset=["close"])
    daily = daily[daily["close"] > 0]
    print(f"Fetched {len(daily)} rows for {picked_date.strftime('%Y-%m-%d')}")
    return daily


def update_history(daily: pd.DataFrame) -> pd.DataFrame:
    DATA_DIR.mkdir(exist_ok=True)
    if HISTORY_PATH.exists():
        history = pd.read_csv(HISTORY_PATH, dtype={"code": str})
    else:
        history = pd.DataFrame(columns=daily.columns)
    combined = pd.concat([history, daily], ignore_index=True)
    combined["code"] = combined["code"].astype(str)
    combined = combined.drop_duplicates(subset=["date", "code"], keep="last")
    combined = combined.sort_values(["code", "date"])
    # Keep around 320 calendar/trading rows per code to control repo size.
    combined = combined.groupby("code", group_keys=False).tail(320)
    combined.to_csv(HISTORY_PATH, index=False, encoding="utf-8-sig")
    return combined


def calc_adx(group: pd.DataFrame, period=14) -> pd.Series:
    high = group["high"].astype(float)
    low = group["low"].astype(float)
    close = group["close"].astype(float)
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
    close = row.get("close", np.nan)
    open_ = row.get("open", np.nan)
    high = row.get("high", np.nan)
    low = row.get("low", np.nan)
    if any(pd.isna(v) for v in [close, open_, high, low]) or high == low:
        return "資料不足"
    body = abs(close - open_)
    upper = high - max(close, open_)
    lower = min(close, open_) - low
    if close < open_ and body / close >= 0.04:
        return "長黑"
    if close > open_ and upper <= body * 0.8:
        return "紅K突破"
    if lower >= body * 1.5:
        return "下影線"
    return "一般K"


def build_scan(history: pd.DataFrame):
    config = load_theme_config()
    manual_themes = config.get("manual_themes", {})
    keywords = config.get("keywords", [])
    out = []
    for code, g in history.groupby("code"):
        g = g.sort_values("date").copy()
        if len(g) < 60:
            continue
        g["ma5"] = g["close"].rolling(5).mean()
        g["ma20"] = g["close"].rolling(20).mean()
        g["ma60"] = g["close"].rolling(60).mean()
        g["ma240"] = g["close"].rolling(240).mean()
        g["avgVolume20"] = g["volume"].rolling(20).mean()
        g["adx"] = calc_adx(g)
        latest = g.iloc[-1].to_dict()
        close_60 = g.iloc[-60]["close"]
        rise60 = ((latest["close"] / close_60) - 1) * 100 if close_60 else np.nan
        theme_text = manual_themes.get(str(code), "")
        theme_hit = any(k.lower() in theme_text.lower() for k in keywords) if theme_text else False
        ma240 = latest.get("ma240")
        if pd.isna(ma240):
            ma240 = 0
        trend = latest["close"] > latest["ma20"] and latest["close"] > latest["ma60"] and (ma240 == 0 or latest["close"] > ma240) and latest["ma20"] >= latest["ma60"]
        fund = latest["avgVolume20"] > 0 and latest["volume"] / latest["avgVolume20"] >= 2
        kline = kline_label(latest)
        price_vol = rise60 >= 30 and latest.get("adx", 0) >= 25 and "長黑" not in kline
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
        if trend: reasons.append("趨勢多頭")
        if fund: reasons.append("量能放大")
        if theme_hit: reasons.append("題材命中")
        if price_vol: reasons.append("價量條件足")
        out.append({
            "date": latest["date"],
            "market": latest.get("market", ""),
            "code": str(code),
            "name": latest.get("name", ""),
            "close": round(float(latest["close"]), 2),
            "ma5": round(float(latest.get("ma5", 0) or 0), 2),
            "ma20": round(float(latest.get("ma20", 0) or 0), 2),
            "ma60": round(float(latest.get("ma60", 0) or 0), 2),
            "ma240": round(float(ma240 or 0), 2),
            "rise60": round(float(rise60), 2),
            "volume": int(latest.get("volume", 0) or 0),
            "avgVolume20": int(latest.get("avgVolume20", 0) or 0),
            "adx": round(float(latest.get("adx", 0) or 0), 2),
            "kline": kline,
            "theme": theme_text,
            "trend": trend,
            "fund": fund,
            "themeHit": theme_hit,
            "priceVol": price_vol,
            "score": score,
            "status": status,
            "reason": "、".join(reasons) if reasons else "條件不足",
        })
    candidates = sorted(out, key=lambda x: (x["score"], x["rise60"], x["volume"]), reverse=True)
    return candidates


def write_outputs(candidates):
    DATA_DIR.mkdir(exist_ok=True)
    meta = {
        "updatedAt": today_taipei().isoformat(),
        "total": len(candidates),
        "qualified3Plus": sum(1 for x in candidates if x["score"] >= 3),
        "qualified4": sum(1 for x in candidates if x["score"] >= 4),
    }
    payload = {"meta": meta, "stocks": candidates}
    LATEST_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if candidates:
        keys = list(candidates[0].keys())
        with LATEST_CSV.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(candidates)
    else:
        LATEST_CSV.write_text("", encoding="utf-8-sig")


def main():
    daily = fetch_recent_trading_day()
    history = update_history(daily)
    candidates = build_scan(history)
    write_outputs(candidates)
    print(f"Wrote {len(candidates)} scan rows to {LATEST_JSON}")


if __name__ == "__main__":
    main()
