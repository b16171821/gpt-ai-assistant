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
MIN_TURNOVER = 50_000_000
MIN_AVG_TURNOVER20 = 30_000_000
MAX_VOLATILITY20_PCT = 8.5
MAX_SAFE_STOP_PCT = 5
MAX_WATCH_STOP_PCT = 8
MIN_SAFE_RR = 2
MIN_WATCH_RR = 1.5
PERIOD = "1y"
BATCH_SIZE = 80
MAX_CODES = 1200
TW_CLOSE_CONFIRM_MINUTE = 14 * 60 + 30


def now_tw_dt():
    return datetime.now(TAIPEI_TZ)


def now_tw():
    return now_tw_dt().isoformat()


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


def trim_to_completed_daily_bars(df):
    """只保留完整收盤日K；若 yfinance 抓到今天盤中未完成日K，直接移除。"""
    if df is None or df.empty:
        return df
    x = df.dropna(subset=["Close"]).copy()
    if x.empty:
        return x
    x.index = pd.to_datetime(x.index)
    now = now_tw_dt()
    minutes = now.hour * 60 + now.minute
    today = now.date()
    latest_date = x.index[-1].date()
    if latest_date >= today and minutes < TW_CLOSE_CONFIRM_MINUTE:
        x = x[x.index.date < today]
    return x.dropna(subset=["Close"])


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
    prior = r.iloc[:-1].copy()
    latest = r.iloc[-1]
    high20 = sf(prior["High"].tail(20).max())
    high60 = sf(prior["High"].max())
    low20 = sf(prior["Low"].tail(20).min())
    low60 = sf(prior["Low"].min())
    avg20v = sf(r["Volume"].tail(20).mean())
    volume = sf(latest["Volume"])
    vol_ratio = volume / avg20v if avg20v else 0
    neckline = high20
    prev_neck = r["High"].shift(1).rolling(20).max()
    avg20v_series = r["Volume"].rolling(20).mean()
    prior_breakouts = (
        (r["Close"] >= prev_neck * 1.02)
        & (r["Volume"] >= avg20v_series * MIN_VOLUME_RATIO)
    ).fillna(False)
    recent_breakouts = prior_breakouts.iloc[:-1].tail(10)
    recent_breakout = bool(recent_breakouts.any())
    if recent_breakout:
        breakout_idx = recent_breakouts[recent_breakouts].index[-1]
        locked_neckline = sf(prev_neck.loc[breakout_idx])
        if locked_neckline > 0:
            neckline = locked_neckline
    support = max(low20, sf(r["Close"].tail(20).mean()) * 0.95)
    compression = (high20 - low20) / close * 100 if close else 999
    distance_from_neckline = (close - neckline) / neckline * 100 if neckline else 999
    distance_from_prev_high = abs(close - high60) / high60 * 100 if high60 else 999
    upper_shadow_pct = (sf(latest["High"]) - max(close, sf(latest["Open"]))) / close * 100 if close else 999
    lower_break_pct = (neckline - sf(latest["Low"])) / neckline * 100 if neckline else 999
    if compression <= 12 and close >= neckline * 0.98:
        pattern = "箱型突破"
    elif compression <= 18 and vol_ratio >= 1.5:
        pattern = "三角收斂突破"
    elif close > sf(r["Close"].tail(10).mean()) and low20 > low60 * 1.05:
        pattern = "旗型整理突破"
    else:
        pattern = "接近成形"
    breakout_confirmed = close >= neckline * 1.02 and vol_ratio >= 2 and upper_shadow_pct <= 3
    pullback_confirmed = (
        recent_breakout
        and close >= neckline * 0.995
        and abs(distance_from_neckline) <= 3
        and lower_break_pct <= 2
        and vol_ratio >= 0.8
        and upper_shadow_pct <= 4
    )
    stage = "突破第一根" if breakout_confirmed else ("回踩頸線不破" if pullback_confirmed else "接近成形＋可觀察")
    target = neckline + (neckline - low60)
    entry_breakout = neckline * 1.02
    entry_pullback = neckline
    chase_low = entry_breakout
    chase_high = entry_breakout * 1.03
    stop_loss = neckline * 0.97
    observation_entry = close if (breakout_confirmed or pullback_confirmed) else entry_breakout
    risk_amount = max(0, observation_entry - stop_loss)
    reward_amount = max(0, target - observation_entry)
    risk_pct = risk_amount / observation_entry * 100 if observation_entry else 999
    reward_pct = reward_amount / observation_entry * 100 if observation_entry else 0
    risk_reward = reward_amount / risk_amount if risk_amount else 0
    near_target = bool(target and close >= target * 0.95)
    forbidden = distance_from_neckline > 5 or distance_from_prev_high > 10 or near_target
    if near_target:
        win_rate = "低"
        plan_status = "接近目標區，只給持有者分批停利參考，空手不追高"
    elif breakout_confirmed and not forbidden:
        win_rate = "高"
        plan_status = "突破確認，可在追價範圍內掛單"
    elif pullback_confirmed and not forbidden:
        win_rate = "高" if risk_reward >= MIN_SAFE_RR and risk_pct <= MAX_SAFE_STOP_PCT else "中"
        plan_status = "回踩頸線不破，可觀察低風險進場"
    elif forbidden:
        win_rate = "低"
        plan_status = "距離頸線或前高過遠，禁止追高"
    else:
        win_rate = "中"
        plan_status = "預備單，等待確認條件"
    confirm = f"頸線以當日前20日高點計算；收盤站上頸線 {sr(neckline)} 至少2%；成交量>=20日均量2倍；不可長上影；回踩須有近10日突破紀錄且不有效跌破頸線；距離頸線>5%禁止進場"
    return {
        "pattern": pattern, "neckline": sr(neckline), "support": sr(support), "target": sr(target), "stage": stage,
        "entryBreakout": sr(entry_breakout), "entryPullback": sr(entry_pullback), "chaseRangeLow": sr(chase_low),
        "chaseRangeHigh": sr(chase_high), "stopLoss": sr(stop_loss), "riskPct": sr(risk_pct), "winRate": win_rate,
        "distanceFromNecklinePct": sr(distance_from_neckline), "distanceFromPrevHighPct": sr(distance_from_prev_high),
        "riskRewardRatio": sr(risk_reward), "rewardPct": sr(reward_pct), "observationEntry": sr(observation_entry),
        "upperShadowPct": sr(upper_shadow_pct), "recentBreakout": bool(recent_breakout), "nearTarget": bool(near_target),
        "forbiddenChase": bool(forbidden), "planStatus": plan_status, "confirmConditions": confirm,
    }


def safety_profile(row):
    dist = abs(sf(row.get("distanceFromNecklinePct"), 999))
    stop_pct = sf(row.get("riskPct"), 999)
    rr = sf(row.get("riskRewardRatio"))
    liquidity_ok = bool(row.get("liquidityOk"))
    volatility_ok = bool(row.get("volatilityOk"))
    forbidden = bool(row.get("forbiddenChase"))
    near_target = bool(row.get("nearTarget"))
    stage = row.get("stage", "")

    score = 0
    if dist <= 3:
        score += 30
    elif dist <= 5:
        score += 15
    if stop_pct <= MAX_SAFE_STOP_PCT:
        score += 20
    elif stop_pct <= MAX_WATCH_STOP_PCT:
        score += 10
    if rr >= MIN_SAFE_RR:
        score += 25
    elif rr >= MIN_WATCH_RR:
        score += 12
    if liquidity_ok:
        score += 15
    if volatility_ok:
        score += 10
    if forbidden or near_target:
        score -= 35

    if near_target or forbidden:
        grade = "C"
        category = "禁止追高"
        action = "已漲遠或接近目標，空手不追；持有者依策略分批處理。"
    elif (
        score >= 85
        and row.get("strictOkBase")
        and stop_pct <= MAX_SAFE_STOP_PCT
        and rr >= MIN_SAFE_RR
        and liquidity_ok
        and volatility_ok
    ):
        grade = "A"
        category = "安全回踩" if "回踩" in stage else "剛突破"
        action = "主升段條件完整且風險較小，可列最高觀察。"
    elif score >= 55 and not forbidden and liquidity_ok and rr >= MIN_WATCH_RR:
        grade = "B"
        category = "即將突破觀察" if "接近" in stage else "等確認"
        action = "條件接近完整，等收盤確認或回踩不破。"
    else:
        grade = "C"
        category = "等更安全位置"
        action = "條件或風險不夠好，先不列入高關注。"
    return {"safeScore": int(max(0, min(100, score))), "grade": grade, "category": category, "action": action}


def analyze_one(df, meta, theme_config):
    if df is None or df.empty:
        return None
    df = trim_to_completed_daily_bars(df)
    if df is None or df.empty:
        return None
    if len(df) < MIN_HISTORY_ROWS:
        return None
    df["MA5"] = df["Close"].rolling(5).mean()
    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA60"] = df["Close"].rolling(60).mean()
    df["MA240"] = df["Close"].rolling(240).mean()
    df["AVG_VOLUME20"] = df["Volume"].rolling(20).mean()
    df["TURNOVER"] = df["Close"] * df["Volume"]
    df["AVG_TURNOVER20"] = df["TURNOVER"].rolling(20).mean()
    df["VOLATILITY20"] = df["Close"].pct_change().rolling(20).std() * 100
    df["ADX"] = calc_adx(df)
    latest = df.iloc[-1]
    close_60 = sf(df.iloc[-60]["Close"])
    close = sf(latest["Close"])
    if close <= 0 or close_60 <= 0:
        return None
    rise60 = (close / close_60 - 1) * 100
    ma20, ma60, ma240 = sf(latest["MA20"]), sf(latest["MA60"]), sf(latest["MA240"])
    avg_volume20, volume, adx = sf(latest["AVG_VOLUME20"]), sf(latest["Volume"]), sf(latest["ADX"])
    turnover = close * volume
    avg_turnover20 = sf(latest["AVG_TURNOVER20"])
    volatility20 = sf(latest["VOLATILITY20"])
    liquidity_ok = turnover >= MIN_TURNOVER and avg_turnover20 >= MIN_AVG_TURNOVER20
    volatility_ok = volatility20 <= MAX_VOLATILITY20_PCT
    theme_text = theme_config.get("manual_themes", {}).get(meta["code"], "")
    keywords = theme_config.get("keywords", [])
    theme_hit = bool(theme_text) and any(k.lower() in theme_text.lower() for k in keywords)
    trend = close > ma20 and close > ma60 and (ma240 == 0 or close > ma240) and ma20 >= ma60
    fund = avg_volume20 > 0 and volume / avg_volume20 >= MIN_VOLUME_RATIO
    kline = kline_label(latest)
    price_vol = rise60 >= MIN_RISE_60 and adx >= MIN_ADX and "長黑" not in kline
    score = int(trend) + int(fund) + int(theme_hit) + int(price_vol)
    plan = pattern_plan(df)
    strict_ok_base = score >= 3 and not plan.get("forbiddenChase", True) and plan.get("stage") in ["突破第一根", "回踩頸線不破"]
    reasons = []
    if trend: reasons.append("趨勢多頭")
    if fund: reasons.append("量能放大")
    if theme_hit: reasons.append("題材命中")
    if price_vol: reasons.append("價量條件足")
    strategy_date = str(df.index[-1].date())
    base = {
        "date": strategy_date, "strategyAsOfDate": strategy_date, "priceType": "收盤確認價",
        "source": "Yahoo Finance via yfinance（日K完整收盤資料）", "dataPolicy": "只使用完整收盤日K，不使用盤中未完成K",
        "market": meta["market"], "code": meta["code"], "name": meta["name"], "close": sr(close),
        "ma5": sr(latest["MA5"]), "ma20": sr(ma20), "ma60": sr(ma60), "ma240": sr(ma240), "rise60": sr(rise60),
        "volume": int(volume), "avgVolume20": int(avg_volume20), "turnover": int(turnover),
        "avgTurnover20": int(avg_turnover20), "volatility20Pct": sr(volatility20),
        "liquidityOk": bool(liquidity_ok), "volatilityOk": bool(volatility_ok),
        "adx": sr(adx), "kline": kline, "theme": theme_text,
        "trend": bool(trend), "fund": bool(fund), "themeHit": bool(theme_hit), "priceVol": bool(price_vol),
        "score": int(score), "strictOkBase": bool(strict_ok_base), "status": "只觀察", "reason": "、".join(reasons) if reasons else "條件不足",
    }
    base.update(plan)
    base.update(safety_profile(base))
    strict_ok = base["grade"] == "A"
    if strict_ok:
        status = "A級安全候選：主升段成立且風險較可控"
    elif base["grade"] == "B":
        status = "B級觀察：接近成形，等待確認"
    elif base["category"] == "禁止追高":
        status = "C級：已漲遠或接近目標，禁止追高"
    elif score >= 3:
        status = "C級觀察：條件或風險尚未達標"
    else:
        status = "只觀察"
    base["strictOk"] = bool(strict_ok)
    base["status"] = status
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
    grade_rank = {"A": 3, "B": 2, "C": 1}
    results = sorted(
        results,
        key=lambda x: (
            grade_rank.get(x.get("grade"), 0),
            x.get("safeScore", 0),
            x.get("strictOk", False),
            x["score"],
            x.get("riskRewardRatio", 0),
            -abs(x.get("distanceFromNecklinePct", 999)),
            x["volume"],
        ),
        reverse=True,
    )
    return results, errors, len(stock_master)


def card(row, idx):
    action = row.get("action") or ("可研究進場" if row.get("strictOk") else "先觀察，等確認")
    return f"""
## {idx}. {row.get('name')}（{row.get('code')}）｜{row.get('grade')}級｜安全分 {row.get('safeScore')}｜{row.get('score')}/4燈

**一句話策略：** {action}。{row.get('planStatus')}

**資料時間**
- 策略依據：{row.get('strategyAsOfDate')} 收盤資料
- 價格類型：{row.get('priceType')}
- 資料來源：{row.get('source')}

**現在位置**
- 資料價格：{row.get('close')}（資料日：{row.get('date')}）
- 型態：{row.get('pattern')}｜階段：{row.get('stage')}
- 分類：{row.get('category')}｜狀態：{row.get('status')}
- 原因：{row.get('reason')}
- 題材：{row.get('theme') or '尚未標註題材'}

**安全檢查**
- 觀察價：{row.get('observationEntry')}
- 停損距離：{row.get('riskPct')}%
- 潛在報酬：{row.get('rewardPct')}%
- 風險報酬比：{row.get('riskRewardRatio')}
- 成交金額：{row.get('turnover')}｜20日均成交金額：{row.get('avgTurnover20')}
- 20日波動：{row.get('volatility20Pct')}%

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
    grade_a = [r for r in rows if r.get("grade") == "A"]
    grade_b = [r for r in rows if r.get("grade") == "B"]
    no_chase = [r for r in rows if r.get("category") == "禁止追高"]
    top = (grade_a + grade_b)[:10]
    conclusion = "有A級安全候選，仍須等收盤與隔日不跌破頸線確認。" if grade_a else ("目前沒有A級安全候選，優先看B級等確認，不追高。" if grade_b else "目前沒有安全主升段候選，不硬選。")
    lines = [
        "# 台股四燈型態學策略報告", "",
        f"策略依據：{meta.get('strategyAsOfDate')} 收盤資料",
        f"系統更新：{meta.get('updatedAt')}", "",
        f"## 結論：{conclusion}", "",
        f"- 掃描檔數：{meta.get('totalAnalyzed')}", f"- 3燈以上：{meta.get('qualified3Plus')}",
        f"- 4燈：{meta.get('qualified4')}", f"- A級安全候選：{len(grade_a)}", f"- B級觀察：{len(grade_b)}", f"- 禁止追高：{len(no_chase)}", "",
        "## 先看這裡", "- 頸線改用當日前20日高點，不包含當日高點。", "- A級才是最高觀察；B級等確認；C級不追。", "- 回踩確認必須有近10日突破紀錄，不能把單日衝高誤判成回踩。", "- 高關注需要成交金額、波動、停損距離與風險報酬比同時過關。", "", "---",
    ]
    if not top:
        lines.append("目前無符合條件標的。")
    else:
        for i, r in enumerate(top, 1):
            lines.append(card(r, i))
            lines.append("---")
    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")


def collect_fieldnames(rows, fallback):
    fieldnames = []
    seen = set()
    for row in rows:
        for name in row.keys():
            if name not in seen:
                seen.add(name)
                fieldnames.append(name)
    return fieldnames or fallback


def write_outputs(rows, errors, total_master):
    DATA_DIR.mkdir(exist_ok=True)
    qualified = [r for r in rows if r["score"] >= 3]
    strict = [r for r in rows if r.get("strictOk")]
    grade_a = [r for r in rows if r.get("grade") == "A"]
    grade_b = [r for r in rows if r.get("grade") == "B"]
    no_chase = [r for r in rows if r.get("category") == "禁止追高"]
    strategy_date = max([r.get("strategyAsOfDate", "") for r in rows], default="")
    meta = {"updatedAt": now_tw(), "strategyAsOfDate": strategy_date, "priceType": "收盤確認價", "dataPolicy": "只使用完整收盤日K，不使用盤中未完成K", "mode": "tw-safe-uptrend-v2", "totalMaster": total_master, "totalAnalyzed": len(rows), "qualified3Plus": len(qualified), "qualified4": sum(1 for r in rows if r["score"] >= 4), "strictCandidates": len(strict), "gradeA": len(grade_a), "gradeB": len(grade_b), "forbiddenChaseCount": len(no_chase), "minTurnover": MIN_TURNOVER, "minAvgTurnover20": MIN_AVG_TURNOVER20, "maxVolatility20Pct": MAX_VOLATILITY20_PCT, "errors": errors[:50], "note": "正式策略僅依據上一個完整收盤交易日；僅供觀察，不構成買賣建議。"}
    payload = {"meta": meta, "stocks": rows}
    LATEST_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    fieldnames = collect_fieldnames(rows, ["date", "code", "name", "score", "status", "reason"])
    with LATEST_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    with HISTORY_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
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
