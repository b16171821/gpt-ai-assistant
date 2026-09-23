"""Read-only evidence for the defensive-market observation overlay."""
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from tw_official_data import fetch_twse_index_history
from update_strategy_tracking import ACTIVE_STATUSES, download_price_frames
from update_tw_stock_scanner import MIN_TURNOVER

ROOT = Path(__file__).resolve().parents[1]


def quote_symbol(row):
    suffix = {"上市": ".TW", "上櫃": ".TWO"}.get(row.get("market"))
    return str(row["code"]) + suffix if suffix and row.get("code") else None


def return_window(frame, as_of):
    if frame is None or frame.empty:
        raise ValueError("沒有完整日 K")
    frame = frame.dropna(subset=["Close"]).sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]
    frame = frame[[str(index)[:10] <= as_of for index in frame.index]]
    if len(frame) < 6 or str(frame.index[-1])[:10] != as_of:
        raise ValueError("缺少同日收盤或近 5 日資料")
    window = frame.tail(6)
    values = [float(value) for value in window["Close"]]
    if any(not math.isfinite(value) or value <= 0 for value in values):
        raise ValueError("收盤數值無效")
    return [str(index)[:10] for index in window.index], (values[-1] / values[0] - 1) * 100


def stock_evidence(frame, benchmark, as_of, latest):
    dates, stock_return = return_window(frame, as_of)
    market_dates, market_return = return_window(benchmark, as_of)
    if dates != market_dates:
        raise ValueError("個股與大盤的 5 日期間不一致")
    close = float(latest.get("close") or 0)
    final = frame[[str(index)[:10] == as_of for index in frame.index]].iloc[-1]
    if abs(float(final["Close"]) - close) > max(0.011, close * 0.0001):
        raise ValueError("行情來源與策略收盤不一致")
    turnover = latest.get("turnover")
    if turnover is None:
        turnover = close * float(latest.get("volume") or 0)
    if not math.isfinite(float(turnover)):
        raise ValueError("成交金額無效")
    return {"status": "COMPLETE", "asOfDate": as_of, "windowDates": dates,
            "stockReturn5Pct": round(stock_return, 4),
            "marketReturn5Pct": round(market_return, 4),
            "turnover": turnover, "minimumTurnover": MIN_TURNOVER,
            "liquidityOk": turnover >= MIN_TURNOVER,
            "source": "Yahoo Finance 完整日K／臺灣證券交易所加權指數"}


def main():
    data = ROOT / "data"
    tw = json.loads((data / "latest.json").read_text(encoding="utf-8-sig"))
    tracking = json.loads((data / "strategy_tracking.json").read_text(encoding="utf-8-sig"))
    as_of = tw.get("meta", {}).get("officialDataDate") or tw.get("meta", {}).get("strategyAsOfDate")
    all_rows = {str(row["code"]): row for row in tw.get("stocks", []) if row.get("code")}
    requested = {code: row for code, row in all_rows.items() if float(row.get("safeScore") or 0) >= 85}
    for record in tracking.get("records", []):
        if record.get("market") == "台股" and record.get("trackingStatus") in ACTIVE_STATUSES:
            code = record["stockCode"]
            if code in all_rows:
                requested[code] = all_rows[code]
    result = {"meta": {"updatedAt": datetime.now(timezone.utc).isoformat(), "asOfDate": as_of}, "records": {}}
    try:
        benchmark = fetch_twse_index_history(as_of, months=2)
        return_window(benchmark, as_of)
        symbols = sorted({quote_symbol(row) for row in requested.values() if quote_symbol(row)})
        frames = download_price_frames(symbols, period="1mo")
        for code, row in requested.items():
            try:
                evidence = stock_evidence(frames.get(quote_symbol(row)), benchmark, as_of, row)
            except Exception as exc:
                evidence = {"status": "UNAVAILABLE", "asOfDate": as_of, "reason": str(exc)}
            result["records"]["台股:" + code] = evidence
    except Exception as exc:
        result["meta"]["error"] = str(exc)
    # Replace even on failure so previous evidence is never presented as current.
    destination = data / "tracking_context.json"
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)
    print("Tracking context:", len(result["records"]), "records", result["meta"].get("error", ""))


if __name__ == "__main__":
    main()
