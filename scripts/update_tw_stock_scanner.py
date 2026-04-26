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
REPORT_MD = DATA_DIR / "latest_report.md"
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
    return rows[:MAX_CODES]


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
    return {
        "pattern": pattern,
        "neckline": sr(neckline),
        "support": sr(support),
        "target": sr(target),
        "stage": stage,
        "entryBreakout": sr(entry_breakout),
        "entryPullback": sr(entry_pullback),
        "chaseRangeLow": sr(chase_low),
        "chaseRangeHigh": sr(chase_high),
        "stopLoss": sr(stop_loss),
        "riskPct": sr(risk_pct),
        "winRate": win_rate,
        "distanceFromNecklinePct": sr(distance_from_neckline),
        "distanceFromPrevHighPct": sr(distance_from_prev_high),
        "forbiddenChase": bool(forbidden),
        "planStatus": plan_status,
        "confirmConditions": confirm,
    }


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
    close_60 = sf(df.iloc[-60]["Close"])
    close = sf(latest["Close"])
    if close <= 0 or close_60 <= 0:
        return None
    rise60 = (close / close_60 - 1) * 100
    ma20, ma60, ma240 = sf(latest["MA20"]), sf(latest["MA60"]), sf(latest["MA240"])
    avg_volume20, volume, adx = sf(latest["AVG_VOLUME20"]), sf(latest["Volume"]), sf(latest["ADX"])
    theme_text = theme_config.get("manual_themes", {}).get(meta["code"], "")
    keywords = theme_config.get("keywords", [])
    theme_hit = bool(theme_text) and any(k.lower() in theme_text.lower() for k in keywords)
    trend = close > ma20 and close > ma60 and (ma240 == 0 or close > ma240) and ma20 >= ma60
    fund = avg_volume20 > 0 and volume / avg_volume20 >= MIN_VOLUME_RATIO
    kline = kline_label(latest)
    price_vol = rise60 >= MIN_RISE_60 and adx >= MIN_ADX and "長黑" not in kline
    score = int(trend) + int(fund) + int(theme_hit) + int(price_vol)
    plan = pattern_plan(df)
    strict_ok = score >= 3 and not plan.get("forbiddenChase", True) and plan.get("stage") in ["突破第一根", "回踩頸線不破"]
    if strict_ok and score >= 4:
        status = "高勝率候選：符合四燈＋型態確認"
    elif strict_ok:
        status = "可觀察進場：三燈＋型態確認"
    elif score >= 3:
        status = "預備單：接近成形，等待確認"
    else:
        status = "只觀察"
    reasons = []
    if trend: reasons.append("趨勢多頭")
    if fund: reasons.append("量能放大")
    if theme_hit: reasons.append("題材命中")
    if price_vol: reasons.append("價量條件足")
    base = {
        "date": str(df.index[-1].date()),
        "market": meta["market"],
        "code": meta["code"],
        "name": meta["name"],
        "close": sr(close),
        "ma5": sr(latest["MA5"]),
        "ma20": sr(ma20),
        "ma60": sr(ma60),
        "ma240": sr(ma240),
        "rise60": sr(rise60),
        "volume": int(volume),
        "avgVolume20": int(avg_volume20),
        "adx": sr(adx),
        "kline": kline,
        "theme": theme_text,
        "trend": bool(trend),
        "fund": bool(fund),
        "themeHit": bool(theme_hit),
        "priceVol": bool(price_vol),
        "score": int(score),
        "strictOk": bool(strict_ok),
        "status": status,
        "reason": "、".join(reasons) if reasons else "條件不足",
    }
    base.update(plan)
    return base


def scan_market():
    stock_master = get_stock_master()
    theme_config = load_theme_config()
    by_ticker = {x["ticker"]: x for x in stock_master}
    results, errors = [], []
    tickers = list(by_ticker.keys())
    for i in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[i:i + BATCH_SIZE]
        try:
            data = yf.download(" ".join(batch), period=PERIOD, interval="1d", group_by="ticker", auto_adjust=False, progress=False, threads=True)
        except Exception as e:
            errors.append(f"batch {i}-{i + len(batch)} failed: {e}")
            continue
        for ticker in batch:
            try:
                df = data if len(batch) == 1 else data[ticker] if ticker in data.columns.get_level_values(0) else None
                row = analyze_one(df, by_ticker[ticker], theme_config)
                if row:
                    results.append(row)
            except Exception as e:
                errors.append(f"{ticker} failed: {e}")
    results = sorted(results, key=lambda x: (x.get("strictOk", False), x["score"], x.get("winRate") == "高", x["rise60"], x["volume"]), reverse=True)
    return results, errors, len(stock_master)


def card(row, idx):
    action = "可研究進場" if row.get("strictOk") else "先觀察，等確認"
    if row.get("forbiddenChase"):
        action = "禁止追高"
    return f"""
## {idx}. {row.get('name')}（{row.get('code')}）｜{row.get('score')}/4燈｜{row.get('winRate')}勝率

**一句話策略：** {action}。{row.get('planStatus')}

**現在位置**
- 現價：{row.get('close')}（資料日：{row.get('date')}）
- 型態：{row.get('pattern')}｜階段：{row.get('stage')}
- 原因：{row.get('reason')}
- 題材：{row.get('theme') or '尚未標註題材'}

**關鍵價位**
- 頸線 / 壓力：{row.get('neckline')}
- 支撐：{row.get('support')}
- 滿足點：{row.get('target')}
- 停損：{row.get('stopLoss')}（跌破頸線直接出）

**進場計畫**
- 突破進場：{row.get('entryBreakout')}
- 回踩進場：{row.get('entryPullback')}
- 合理追價範圍：{row.get('chaseRangeLow')} ～ {row.get('chaseRangeHigh')}
- 距離頸線：{row.get('distanceFromNecklinePct')}%｜風險：約 {row.get('riskPct')}%

**確認條件**
{row.get('confirmConditions')}
"""


def write_report(rows, meta):
    strict = [r for r in rows if r.get("strictOk")]
    watch = [r for r in rows if r.get("score", 0) >= 3 and not r.get("strictOk")]
    top = (strict + watch)[:10]
    if strict:
        conclusion = "有符合嚴格條件的候選股，但仍要等隔日不跌破頸線確認。"
    elif watch:
        conclusion = "目前沒有完美進場股，優先列預備單，等突破或回踩確認。"
    else:
        conclusion = "目前無符合條件標的，不硬選。"
    lines = [
        "# 台股四燈型態學策略報告",
        "",
        f"更新時間：{meta.get('updatedAt')}",
        "",
        f"## 結論：{conclusion}",
        "",
        f"- 掃描檔數：{meta.get('totalAnalyzed')}",
        f"- 3燈以上：{meta.get('qualified3Plus')}",
        f"- 4燈：{meta.get('qualified4')}",
        f"- 嚴格進場候選：{meta.get('strictCandidates')}",
        "",
        "## 先看這裡",
        "- strictOk = True：才是比較接近可操作的候選。",
        "- forbiddenChase = True：禁止追高，只能等回踩。",
        "- planStatus：直接看它告訴你要做什麼。",
        "- 合理追價範圍：確認後才可用，不是現在直接追。",
        "",
        "---",
    ]
    if not top:
        lines.append("目前無符合條件標的。")
    else:
        for i, r in enumerate(top, 1):
            lines.append(card(r, i))
            lines.append("---")
    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")


def write_outputs(rows, errors, total_master):
    DATA_DIR.mkdir(exist_ok=True)
    qualified = [r for r in rows if r["score"] >= 3]
    strict = [r for r in rows if r.get("strictOk")]
    meta = {"updatedAt": now_tw(), "mode": "pattern-four-lights-scan", "totalMaster": total_master, "totalAnalyzed": len(rows), "qualified3Plus": len(qualified), "qualified4": sum(1 for r in rows if r["score"] >= 4), "strictCandidates": len(strict), "errors": errors[:50], "note": "四燈＋型態學＋確認條件＋追價範圍＋風控；僅供觀察，不構成買賣建議。"}
    payload = {"meta": meta, "stocks": rows}
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
    write_report(rows, meta)


def write_failure(e):
    DATA_DIR.mkdir(exist_ok=True)
    payload = {"meta": {"updatedAt": now_tw(), "mode": "failed-safe", "error": str(e)}, "stocks": []}
    LATEST_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    LATEST_CSV.write_text("date,code,name,score,status,reason\n", encoding="utf-8-sig")
    HISTORY_CSV.write_text("date,code,name,score,status,reason\n", encoding="utf-8-sig")
    REPORT_MD.write_text(f"# 台股四燈型態學策略報告\n\n流程失敗：{e}\n", encoding="utf-8")
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
