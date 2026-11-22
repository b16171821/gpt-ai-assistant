import csv
import json
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import twstock
import yfinance as yf

import apo_five_lights as five
from market_filter import apply_breadth_guard, apply_market_filter_to_rows, classify_market_regime, previous_a_count_from_snapshots
from tw_official_data import (
    OfficialDataError,
    fetch_tpex_index_history,
    fetch_twse_index_history,
    load_latest_official_quotes,
    merge_official_bar,
    validate_official_coverage,
)

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
LATEST_JSON = DATA_DIR / "latest.json"
LATEST_CSV = DATA_DIR / "latest.csv"
HISTORY_CSV = DATA_DIR / "history.csv"
REPORT_MD = DATA_DIR / "latest_report.md"
THEME_PATH = DATA_DIR / "theme_keywords.json"
SNAPSHOTS_PATH = DATA_DIR / "strategy_snapshots.json"
TAIPEI_TZ = timezone(timedelta(hours=8))

# 60 日漲幅只做「強勢股候選」標籤，不再是買進條件。數值定義在 apo_five_lights。
MIN_RISE_60 = five.STRONG_STOCK_RISE60
MIN_VOLUME_RATIO = five.VOLUME_BREAKOUT_MULTIPLE
MIN_ADX = five.ADX_MIN
MIN_HISTORY_ROWS = five.MIN_BARS
MIN_TURNOVER = five.TW_MIN_TURNOVER
MIN_AVG_TURNOVER20 = five.TW_MIN_AVG_TURNOVER20
MAX_VOLATILITY20_PCT = 8.5
MAX_SAFE_STOP_PCT = five.STOP_REFERENCE_PCT * 100
MAX_WATCH_STOP_PCT = 8
MIN_SAFE_RR = five.MIN_REWARD_RISK
MIN_WATCH_RR = 1.5
PERIOD = "3y"
BATCH_SIZE = 80
# 不再截斷。全市場＝ twstock 四碼普通股，依代號排序。
MAX_CODES = None
MIN_OFFICIAL_COVERAGE_PCT = 95
UNIVERSE_POLICY = "全部 twstock 四碼普通股（上市與上櫃），依代號排序，不截斷"
TW_CLOSE_CONFIRM_MINUTE = 14 * 60 + 30
TW_INDEX_SYMBOLS = {"加權指數": ["^TWII"], "櫃買指數": ["TWOII.TWO", "^TWOII"]}


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
    rows.sort(key=lambda item: item["code"])
    return rows


def calc_adx(df, period=14):
    """Wilder ADX。+DM 與 -DM 打平時兩邊都是 0，不再用簡單平均。"""
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
    """相容舊呼叫點。評級只來自 apo_five_lights，這裡不再用絕對值距離判追高。"""
    row = five.analyze_ohlcv(df, market="台股", symbol="", name="")
    return {} if not row else row


def safety_profile(row):
    """不再重算 A/B/C。沿用五燈已經寫好的評級。"""
    rating = row.get("rating") or "未核實"
    return {
        "safeScore": int(row.get("safeScore") or 0),
        "grade": rating,
        "category": row.get("category") or rating,
        "action": row.get("action") or "",
    }


def analyze_one(df, meta, theme_config):
    if df is None or df.empty:
        return None
    df = five.flatten_price_columns(trim_to_completed_daily_bars(df))
    if df is None or df.empty:
        return None
    keywords = theme_config.get("keywords", []) if theme_config else []
    theme_text = ""
    if theme_config:
        theme_text = theme_config.get("manual_themes", {}).get(meta["code"], "")
    row = five.analyze_ohlcv(
        df,
        market="台股",
        symbol=meta["code"],
        name=meta["name"],
        theme_text=theme_text,
        keywords=keywords,
        lot_size=five.TW_LOT_SIZE,
        min_turnover=MIN_TURNOVER,
        min_avg_turnover=MIN_AVG_TURNOVER20,
    )
    if not row:
        return None
    row["market"] = meta["market"]
    row["source"] = "Yahoo Finance via yfinance（日K完整收盤資料）"
    row["dataPolicy"] = "只使用完整收盤日K，不使用盤中未完成K。均量用前20個已完成交易日。"
    row["kline"] = kline_label(df.iloc[-1])
    row["trend"] = bool(row["lights"][0]["passed"])
    row["fund"] = bool(row["lights"][2]["passed"])
    row["priceVol"] = bool(row["lights"][1]["passed"] and row["lights"][3]["passed"])
    return row


def download_index(symbols):
    for symbol in symbols:
        try:
            df = yf.download(symbol, period=PERIOD, interval="1d", auto_adjust=False, progress=False, threads=False)
            df = trim_to_completed_daily_bars(df)
            if df is not None and not df.empty:
                return df
        except Exception:
            continue
    return pd.DataFrame()


def build_market_filter(official_date):
    weighted = fetch_twse_index_history(official_date)
    otc = fetch_tpex_index_history(official_date)
    return classify_market_regime(weighted, otc)


def scan_market():
    stock_master = get_stock_master()
    theme_config = load_theme_config()
    official = load_latest_official_quotes()
    coverage = validate_official_coverage(
        [item["code"] for item in stock_master],
        official["quotes"],
        minimum_pct=MIN_OFFICIAL_COVERAGE_PCT,
    )
    market_filter = build_market_filter(official["officialDate"])
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
                meta = by_ticker[ticker]
                quote = official["quotes"].get(meta["code"])
                if not quote:
                    errors.append(f"{ticker} skipped: official close is unavailable for {official['officialDate']}")
                    continue
                df = data if len(batch) == 1 else data[ticker] if ticker in data.columns.get_level_values(0) else None
                df = merge_official_bar(df, quote)
                row = analyze_one(df, by_ticker[ticker], theme_config)
                if row:
                    row["date"] = official["officialDate"]
                    row["strategyAsOfDate"] = official["officialDate"]
                    row["officialDataDate"] = official["officialDate"]
                    row["dataFresh"] = True
                    row["priceVerified"] = True
                    row["source"] = "臺灣證券交易所 / 證券櫃檯買賣中心正式收盤；yfinance 歷史資料"
                    results.append(row)
            except Exception as e:
                errors.append(f"{ticker} failed: {e}")
    if not results:
        raise OfficialDataError(f"{official['officialDate']} 沒有任何完成官方收盤覆蓋的分析資料")
    stale_rows = [
        row for row in results
        if row.get("strategyAsOfDate") != official["officialDate"] or not row.get("dataFresh")
    ]
    if stale_rows:
        raise OfficialDataError(
            f"分析結果混入 {len(stale_rows)} 筆非 {official['officialDate']} 的資料，已停止發布"
        )
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
    market_filter = apply_breadth_guard(market_filter, results, previous_a_count_from_snapshots(SNAPSHOTS_PATH, "台股"))
    results = apply_market_filter_to_rows(results, market_filter)
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
    freshness = {
        **coverage,
        "officialDataDate": official["officialDate"],
        "officialQuoteCount": official["officialQuoteCount"],
        "listedOfficialCount": official["listedCount"],
        "otcOfficialCount": official["otcCount"],
        "freshCount": len(results),
        "staleCount": 0,
        "source": official["source"],
    }
    return results, errors, len(stock_master), market_filter, freshness


def card(row, idx):
    action = row.get("action") or ("可研究進場" if row.get("strictOk") else "先觀察，等確認")
    return f"""
## {idx}. {row.get('name')}（{row.get('code')}）｜{row.get('rating')}｜{row.get('lightsPassed')}/5燈

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


def write_report(rows, meta, market_filter):
    ready = [r for r in rows if r.get("rawRating") == "待執行名單"]
    watch = [r for r in rows if r.get("rawRating") in {"高優先觀察", "觀察"}]
    top = (ready + watch)[:10]
    regime = market_filter.get("marketRegime", "WATCH")
    regime_label = market_filter.get("regimeLabel", "")
    if regime == "ATTACK":
        conclusion = "有五燈待執行名單，仍須大盤維持進攻且價格留在進場區。" if ready else ("目前沒有五燈正式訊號，優先看四燈與三燈觀察。" if watch else "目前沒有可執行訊號，不硬選。")
    else:
        conclusion = f"大盤為 {regime} {regime_label}，正式進場訊號降級，停止積極進場。"
    lines = [
        f"# {five.SYSTEM_LABEL}台股報告", "",
        "勝率：尚未回測。本報告不顯示未經驗證的勝率。", "",
        f"策略依據：{meta.get('strategyAsOfDate')} 收盤資料",
        f"系統更新：{meta.get('updatedAt')}", "",
        f"## 今日大盤狀態：{regime} {regime_label}",
        f"今日策略：{market_filter.get('strategy')}", "",
        f"## 結論：{conclusion}", "",
        f"- 掃描檔數：{meta.get('totalAnalyzed')}",
        f"- 待執行名單：{meta.get('ratingReady')}",
        f"- 高優先觀察：{meta.get('ratingHigh')}",
        f"- 觀察：{meta.get('ratingWatch')}",
        f"- 延伸在壓力之上：{meta.get('extendedAboveCount')}",
        f"- 跌破確認價：{meta.get('brokeBelowCount')}",
        "",
        "## 先看這裡",
        "- 頸線用當日前 20 日高點，不含當日。",
        "- 均量用前 20 個已完成交易日，不含當日。",
        "- 只有收盤在頸線或前高之上過遠才算延伸；跌破另列，不叫追高。",
        "- MA240 不足時長期趨勢是未核實，不算通過。",
        "- 五燈才是待執行名單。題材與 60 日漲幅只是標籤。",
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


def collect_fieldnames(rows, fallback):
    fieldnames = []
    seen = set()
    for row in rows:
        for name in row.keys():
            if name not in seen:
                seen.add(name)
                fieldnames.append(name)
    return fieldnames or fallback


def _csv_value(value):
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return value


def write_outputs(rows, errors, total_master, market_filter, freshness):
    DATA_DIR.mkdir(exist_ok=True)
    qualified = [r for r in rows if (r.get("lightsPassed") or 0) >= 3]
    strict = [r for r in rows if r.get("strictOk")]
    strategy_date = freshness.get("officialDataDate", "")
    if not strategy_date or any(r.get("strategyAsOfDate") != strategy_date for r in rows):
        raise OfficialDataError("輸出前資料日期一致性檢查失敗，已停止發布")
    meta = {
        "updatedAt": now_tw(),
        "strategyAsOfDate": strategy_date,
        "officialDataDate": strategy_date,
        "strategySystem": five.SYSTEM_NAME,
        "strategyLabel": five.SYSTEM_LABEL,
        "backtestStatus": "尚未回測",
        "priceType": "收盤確認價",
        "dataPolicy": "只使用完整收盤日K。指標用還原價，買賣價用官方原始收盤。均量不含當日。",
        "universePolicy": UNIVERSE_POLICY,
        "mode": "apo-five-lights-1.0",
        "totalMaster": total_master,
        "totalAnalyzed": len(rows),
        "qualified3Plus": len(qualified),
        "qualified4": sum(1 for r in rows if (r.get("lightsPassed") or 0) >= 4),
        "strictCandidates": len(strict),
        "ratingReady": sum(1 for r in rows if r.get("rawRating") == "待執行名單"),
        "ratingHigh": sum(1 for r in rows if r.get("rawRating") == "高優先觀察"),
        "ratingWatch": sum(1 for r in rows if r.get("rawRating") == "觀察"),
        "ratingReject": sum(1 for r in rows if r.get("rawRating") == "不符"),
        "ratingUnverified": sum(1 for r in rows if r.get("rawRating") == "未核實"),
        "extendedAboveCount": sum(1 for r in rows if r.get("extendedAbove")),
        "brokeBelowCount": sum(1 for r in rows if r.get("brokeBelow")),
        "marketRegime": market_filter.get("marketRegime"),
        "marketRegimeLabel": market_filter.get("regimeLabel"),
        "marketStrategy": market_filter.get("strategy"),
        "marketFilter": market_filter,
        "minTurnover": MIN_TURNOVER,
        "minAvgTurnover20": MIN_AVG_TURNOVER20,
        "feeRate": five.FEE_RATE,
        "feeDiscount": five.FEE_DISCOUNT,
        "sellTaxRate": five.SELL_TAX_RATE,
        "stopReferencePct": five.STOP_REFERENCE_PCT * 100,
        "targetReferencePct": five.TARGET_REFERENCE_PCT * 100,
        "errors": errors[:50],
        "freshCount": freshness.get("freshCount", len(rows)),
        "staleCount": freshness.get("staleCount", 0),
        "officialCoveragePct": freshness.get("coveragePct", 0),
        "officialCoveredCount": freshness.get("coveredCount", 0),
        "officialExpectedCount": freshness.get("expectedCount", total_master),
        "officialQuoteCount": freshness.get("officialQuoteCount", 0),
        "listedOfficialCount": freshness.get("listedOfficialCount", 0),
        "otcOfficialCount": freshness.get("otcOfficialCount", 0),
        "officialSource": freshness.get("source", ""),
        "freshnessStatus": "COMPLETE",
        "note": "當日 OHLCV 已以證交所與櫃買中心正式收盤覆蓋，並刪除官方日期之後的 K 棒。勝率尚未回測。",
    }
    payload = {"meta": meta, "marketFilter": market_filter, "stocks": rows}
    LATEST_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    flat_rows = [{key: _csv_value(value) for key, value in row.items()} for row in rows]
    fieldnames = collect_fieldnames(flat_rows, ["date", "code", "name", "score", "status", "reason"])
    with LATEST_CSV.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(flat_rows)
    five.append_history(HISTORY_CSV, rows)
    write_report(rows, meta, market_filter)


def write_failure(e):
    DATA_DIR.mkdir(exist_ok=True)
    payload = {"meta": {"updatedAt": now_tw(), "mode": "failed-safe", "error": str(e), "backtestStatus": "尚未回測"}, "stocks": []}
    LATEST_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    LATEST_CSV.write_text("date,code,name,score,status,reason\n", encoding="utf-8-sig")
    REPORT_MD.write_text(f"# {five.SYSTEM_LABEL}台股報告\n\n流程失敗：{e}\n既有 history.csv 未刪除。\n", encoding="utf-8")
    print(f"FAILED-SAFE: {e}")


def main():
    try:
        rows, errors, total_master, market_filter, freshness = scan_market()
        write_outputs(rows, errors, total_master, market_filter, freshness)
        print(f"Scan complete. analyzed={len(rows)} errors={len(errors)}")
    except Exception as e:
        print(f"Update aborted; previous complete data is preserved: {e}")
        raise


if __name__ == "__main__":
    main()
