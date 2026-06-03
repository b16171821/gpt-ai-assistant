import csv
import json
import math
import urllib.parse
import urllib.request
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
TW_CLOSE_CONFIRM_MINUTE = 14 * 60 + 30


def now_tw_dt():
    return datetime.now(TAIPEI_TZ)


def now_tw():
    return now_tw_dt().isoformat()


def sf(v, default=0.0):
    try:
        if pd.isna(v):
            return default
        s = str(v).replace(",", "").replace("--", "").strip()
        if s == "":
            return default
        return float(s)
    except Exception:
        return default


def sr(v, digits=2):
    v = sf(v)
    return round(v, digits) if math.isfinite(v) else 0


def http_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8", errors="ignore"))


def normalize_quote_row(fields, row, market, trade_date):
    def idx(*names):
        for n in names:
            if n in fields:
                return fields.index(n)
        return -1

    code_i = idx("證券代號", "代號", "股票代號")
    name_i = idx("證券名稱", "名稱", "股票名稱")
    open_i = idx("開盤價", "開盤")
    high_i = idx("最高價", "最高")
    low_i = idx("最低價", "最低")
    close_i = idx("收盤價", "收盤")
    vol_i = idx("成交股數", "成交股數合計", "成交量")
    if code_i < 0 or close_i < 0 or len(row) <= max(code_i, close_i):
        return None
    code = str(row[code_i]).strip()
    close = sf(row[close_i])
    if not code or close <= 0:
        return None
    return {
        "code": code,
        "name": str(row[name_i]).strip() if name_i >= 0 and len(row) > name_i else "",
        "market": market,
        "tradeDate": trade_date,
        "open": sf(row[open_i], close) if open_i >= 0 and len(row) > open_i else close,
        "high": sf(row[high_i], close) if high_i >= 0 and len(row) > high_i else close,
        "low": sf(row[low_i], close) if low_i >= 0 and len(row) > low_i else close,
        "close": close,
        "volume": int(sf(row[vol_i], 0)) if vol_i >= 0 and len(row) > vol_i else 0,
        "source": "TWSE" if market == "上市" else "TPEx",
    }


def parse_tables(payload, market, trade_date):
    quotes = {}
    table_candidates = []
    for key in ("data9", "data", "aaData"):
        if isinstance(payload.get(key), list):
            table_candidates.append((payload.get("fields", []), payload[key]))
    for table in payload.get("tables", []) if isinstance(payload.get("tables"), list) else []:
        fields = table.get("fields") or table.get("headers") or []
        data = table.get("data") or []
        table_candidates.append((fields, data))
    for fields, data in table_candidates:
        if not fields or not isinstance(data, list):
            continue
        for row in data:
            if not isinstance(row, list):
                continue
            q = normalize_quote_row(fields, row, market, trade_date)
            if q:
                quotes[q["code"]] = q
    return quotes


def fetch_twse_quotes(date_obj):
    date_str = date_obj.strftime("%Y%m%d")
    url = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={date_str}&type=ALLBUT0999&response=json"
    payload = http_json(url)
    return parse_tables(payload, "上市", str(date_obj))


def fetch_tpex_quotes_new(date_obj):
    date_str = date_obj.strftime("%Y/%m/%d")
    qs = urllib.parse.urlencode({"date": date_str, "type": "EW", "response": "json"})
    url = f"https://www.tpex.org.tw/www/zh-tw/afterTrading/otc?{qs}"
    payload = http_json(url)
    return parse_tables(payload, "上櫃", str(date_obj))


def fetch_tpex_quotes_old(date_obj):
    roc_year = date_obj.year - 1911
    date_str = f"{roc_year}/{date_obj.month:02d}/{date_obj.day:02d}"
    qs = urllib.parse.urlencode({"l": "zh-tw", "d": date_str, "o": "json", "s": "0,asc,0"})
    url = f"https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/stk_quote_result.php?{qs}"
    payload = http_json(url)
    return parse_tables(payload, "上櫃", str(date_obj))


def fetch_tpex_quotes(date_obj):
    try:
        q = fetch_tpex_quotes_new(date_obj)
        if q:
            return q
    except Exception:
        pass
    try:
        return fetch_tpex_quotes_old(date_obj)
    except Exception:
        return {}


def official_start_date():
    now = now_tw_dt()
    minutes = now.hour * 60 + now.minute
    d = now.date()
    if minutes < TW_CLOSE_CONFIRM_MINUTE:
        d = d - timedelta(days=1)
    return d


def fetch_latest_official_quotes():
    start = official_start_date()
    errors = []
    for i in range(10):
        d = start - timedelta(days=i)
        if d.weekday() >= 5:
            continue
        twse, tpex = {}, {}
        try:
            twse = fetch_twse_quotes(d)
        except Exception as exc:
            errors.append(f"TWSE {d}: {exc}")
        try:
            tpex = fetch_tpex_quotes(d)
        except Exception as exc:
            errors.append(f"TPEx {d}: {exc}")
        if twse or tpex:
            return str(d), twse, tpex, errors
    return "", {}, {}, errors


def trim_to_completed_daily_bars(df):
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


def apply_official_quote(df, quote):
    if df is None or df.empty or not quote:
        return df, None
    x = df.copy()
    x.index = pd.to_datetime(x.index)
    q_date = pd.Timestamp(quote["tradeDate"])
    latest_date = x.dropna(subset=["Close"]).index[-1].date() if not x.dropna(subset=["Close"]).empty else None
    original_close = sf(x.dropna(subset=["Close"]).iloc[-1]["Close"]) if latest_date else 0
    row = {
        "Open": quote["open"],
        "High": quote["high"],
        "Low": quote["low"],
        "Close": quote["close"],
        "Adj Close": quote["close"],
        "Volume": quote["volume"],
    }
    if q_date in x.index:
        for k, v in row.items():
            if k in x.columns:
                x.loc[q_date, k] = v
            else:
                x[k] = np.nan
                x.loc[q_date, k] = v
    else:
        new_row = pd.DataFrame([row], index=[q_date])
        for col in x.columns:
            if col not in new_row.columns:
                new_row[col] = np.nan
        x = pd.concat([x, new_row[x.columns]], axis=0).sort_index()
    audit = {
        "priceVerified": True,
        "officialTradeDate": quote["tradeDate"],
        "officialClose": sr(quote["close"]),
        "yahooCloseBeforeAudit": sr(original_close),
        "officialSource": quote["source"],
    }
    return x, audit


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
        "pattern": pattern, "neckline": sr(neckline), "support": sr(support), "target": sr(target), "stage": stage,
        "entryBreakout": sr(entry_breakout), "entryPullback": sr(entry_pullback), "chaseRangeLow": sr(chase_low),
        "chaseRangeHigh": sr(chase_high), "observationEntry": sr(entry_breakout), "limitPrice": sr(entry_breakout),
        "stopLoss": sr(stop_loss), "riskPct": sr(risk_pct), "winRate": win_rate,
        "distanceFromNecklinePct": sr(distance_from_neckline), "distanceFromPrevHighPct": sr(distance_from_prev_high),
        "forbiddenChase": bool(forbidden), "planStatus": plan_status, "confirmConditions": confirm,
    }


def analyze_one(df, meta, theme_config, audit=None):
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
    strategy_date = str(df.index[-1].date())
    base = {
        "date": strategy_date, "strategyAsOfDate": strategy_date, "priceType": "官方收盤確認價",
        "source": "TWSE / TPEx 官方收盤資料校驗 + Yahoo Finance 日K", "dataPolicy": "台股收盤價以官方交易所資料校驗；不使用盤中未完成K",
        "market": meta["market"], "code": meta["code"], "name": meta["name"], "close": sr(close),
        "ma5": sr(latest["MA5"]), "ma20": sr(ma20), "ma60": sr(ma60), "ma240": sr(ma240), "rise60": sr(rise60),
        "volume": int(volume), "avgVolume20": int(avg_volume20), "volumeRatio": sr(volume / avg_volume20 if avg_volume20 else 0), "adx": sr(adx), "kline": kline, "theme": theme_text,
        "trend": bool(trend), "fund": bool(fund), "themeHit": bool(theme_hit), "priceVol": bool(price_vol),
        "score": int(score), "strictOk": bool(strict_ok), "status": status, "reason": "、".join(reasons) if reasons else "條件不足",
        "priceVerified": bool(audit and audit.get("priceVerified")),
    }
    if audit:
        base.update(audit)
    base.update(plan)
    return base


def scan_market():
    official_date, twse_quotes, tpex_quotes, official_errors = fetch_latest_official_quotes()
    stock_master = get_stock_master()
    theme_config = load_theme_config()
    by_ticker = {x["ticker"]: x for x in stock_master}
    results, errors = [], official_errors[:]
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
                meta = by_ticker[ticker]
                df = data if len(batch) == 1 else data[ticker] if ticker in data.columns.get_level_values(0) else None
                quote = twse_quotes.get(meta["code"]) if "上市" in meta.get("market", "") else tpex_quotes.get(meta["code"])
                audit = None
                if quote:
                    df, audit = apply_official_quote(df, quote)
                else:
                    errors.append(f"{meta['code']} no official quote for {official_date}")
                row = analyze_one(df, meta, theme_config, audit)
                if row:
                    results.append(row)
            except Exception as e:
                errors.append(f"{ticker} failed: {e}")
    results = sorted(results, key=lambda x: (x.get("strictOk", False), x["score"], x.get("winRate") == "高", x["rise60"], x["volume"]), reverse=True)
    return results, errors, len(stock_master), official_date


def card(row, idx):
    action = "可研究進場" if row.get("strictOk") else "先觀察，等確認"
    if row.get("forbiddenChase"):
        action = "禁止追高"
    return f"""
## {idx}. {row.get('name')}（{row.get('code')}）｜{row.get('score')}/4燈｜{row.get('winRate')}勝率

**一句話策略：** {action}。{row.get('planStatus')}

**資料時間**
- 策略依據：{row.get('strategyAsOfDate')} 收盤資料
- 價格類型：{row.get('priceType')}
- 資料來源：{row.get('source')}
- 官方價格校驗：{'已校驗' if row.get('priceVerified') else '未校驗'}

**現在位置**
- 資料價格：{row.get('close')}（資料日：{row.get('date')}）
- 官方收盤價：{row.get('officialClose', '-')}
- yfinance 校驗前收盤：{row.get('yahooCloseBeforeAudit', '-')}
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
    conclusion = "有符合嚴格條件的候選股，但仍要等隔日不跌破頸線確認。" if strict else ("目前沒有完美進場股，優先列預備單，等突破或回踩確認。" if watch else "目前無符合條件標的，不硬選。")
    lines = [
        "# 台股四燈型態學策略報告", "",
        f"策略依據：{meta.get('strategyAsOfDate')} 收盤資料",
        f"官方交易日：{meta.get('officialTradeDate')}",
        f"系統更新：{meta.get('updatedAt')}", "",
        f"## 結論：{conclusion}", "",
        f"- 掃描檔數：{meta.get('totalAnalyzed')}", f"- 3燈以上：{meta.get('qualified3Plus')}",
        f"- 4燈：{meta.get('qualified4')}", f"- 嚴格進場候選：{meta.get('strictCandidates')}",
        f"- 官方價格校驗：{meta.get('priceVerifiedCount')} / {meta.get('totalAnalyzed')}", "",
        "## 先看這裡", "- 台股收盤價以 TWSE / TPEx 官方資料校驗。", "- 若官方資料與 yfinance 不一致，以官方資料為準。", "- strictOk = True：才是比較接近可操作的候選。", "- forbiddenChase = True：禁止追高，只能等回踩。", "- 合理追價範圍：確認後才可用，不是現在直接追。", "", "---",
    ]
    if not top:
        lines.append("目前無符合條件標的。")
    else:
        for i, r in enumerate(top, 1):
            lines.append(card(r, i))
            lines.append("---")
    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")


def write_outputs(rows, errors, total_master, official_date):
    DATA_DIR.mkdir(exist_ok=True)
    qualified = [r for r in rows if r["score"] >= 3]
    strict = [r for r in rows if r.get("strictOk")]
    strategy_date = max([r.get("strategyAsOfDate", "") for r in rows], default="")
    verified_count = sum(1 for r in rows if r.get("priceVerified"))
    meta = {
        "updatedAt": now_tw(), "strategyAsOfDate": strategy_date, "officialTradeDate": official_date,
        "priceType": "官方收盤確認價", "dataPolicy": "台股收盤價以 TWSE / TPEx 官方資料校驗；不使用盤中未完成K",
        "mode": "pattern-four-lights-official-close-verified-scan", "totalMaster": total_master, "totalAnalyzed": len(rows),
        "qualified3Plus": len(qualified), "qualified4": sum(1 for r in rows if r["score"] >= 4),
        "strictCandidates": len(strict), "priceVerifiedCount": verified_count, "errors": errors[:100],
        "note": "正式策略僅依據完整收盤交易日；台股價格以官方資料校驗；僅供觀察，不構成買賣建議。",
    }
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
        rows, errors, total_master, official_date = scan_market()
        write_outputs(rows, errors, total_master, official_date)
        print(f"Scan complete. analyzed={len(rows)} errors={len(errors)} official_date={official_date}")
    except Exception as e:
        write_failure(e)


if __name__ == "__main__":
    main()
