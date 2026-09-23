import argparse
import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "data_health.json"


def issue(level: str, issue_code: str, message: str, **context: Any) -> dict[str, Any]:
    return {"level": level, "code": issue_code, "message": message, "context": context}


def finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def parse_date(value: Any):
    text = str(value or "").strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def find_non_finite(value: Any, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, float) and not math.isfinite(value):
        found.append(path)
    elif isinstance(value, dict):
        for key, child in value.items():
            found.extend(find_non_finite(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(find_non_finite(child, f"{path}[{index}]"))
    return found


def validate_ohlcv_series(rows: list[dict[str, Any]], holidays: set[date] | None = None) -> dict[str, Any]:
    holidays = holidays or set()
    issues: list[dict[str, Any]] = []
    parsed = [parse_date(row.get("date")) for row in rows if isinstance(row, dict)]
    dates = [value for value in parsed if value]
    duplicates = sorted({value.isoformat() for value in dates if dates.count(value) > 1})
    if duplicates:
        issues.append(issue("ERROR", "DUPLICATE_BAR_DATE", "OHLCV 含重複交易日", dates=duplicates))
    missing: list[str] = []
    if dates:
        cursor, end = min(dates), max(dates)
        available = set(dates)
        while cursor <= end:
            if cursor.weekday() < 5 and cursor not in holidays and cursor not in available:
                missing.append(cursor.isoformat())
            cursor += timedelta(days=1)
    if missing:
        issues.append(issue("ERROR", "MISSING_TRADING_DAY", "OHLCV 缺少應有交易日", dates=missing[:50]))
    return {"status": dataset_status(issues), "rowCount": len(rows), "issues": issues}


def validate_stock_payload(payload: dict[str, Any], market: str) -> dict[str, Any]:
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    stocks = payload.get("stocks") if isinstance(payload.get("stocks"), list) else []
    issues: list[dict[str, Any]] = []
    strategy_date = str(
        meta.get("officialDataDate")
        or meta.get("completedDataDate")
        or meta.get("strategyAsOfDate")
        or ""
    )
    if not parse_date(strategy_date):
        issues.append(issue("ERROR", "INVALID_STRATEGY_DATE", "缺少或無法解析策略資料日", value=strategy_date))
    if not meta.get("updatedAt") and not meta.get("updatedAtTW"):
        issues.append(issue("ERROR", "MISSING_UPDATED_AT", "缺少資料更新時間"))
    if not stocks:
        issues.append(issue("ERROR", "EMPTY_STOCKS", "股票資料為空"))

    non_finite = find_non_finite(payload)
    if non_finite:
        issues.append(issue("ERROR", "NON_FINITE_NUMBER", "資料包含 NaN 或 Infinity", paths=non_finite[:20]))

    seen: set[str] = set()
    complete = 0
    for index, stock in enumerate(stocks):
        if not isinstance(stock, dict):
            issues.append(issue("ERROR", "INVALID_ROW", "股票資料列不是物件", index=index))
            continue
        code = str(stock.get("code") or stock.get("ticker") or "").strip()
        name = str(stock.get("name") or "").strip()
        close = stock.get("close")
        row_date = str(stock.get("officialDataDate") or stock.get("dataDate") or stock.get("strategyAsOfDate") or stock.get("date") or "")
        if code in seen:
            issues.append(issue("ERROR", "DUPLICATE_SYMBOL", "股票代號重複", code=code, index=index))
        elif code:
            seen.add(code)
        missing = [
            field
            for field, value in (("code", code), ("name", name), ("close", close), ("date", row_date))
            if value is None or value == ""
        ]
        if missing:
            issues.append(issue("ERROR", "MISSING_REQUIRED_FIELD", "股票資料缺少必要欄位", code=code, fields=missing))
        elif finite_number(close) and close > 0:
            complete += 1
        else:
            issues.append(issue("ERROR", "INVALID_CLOSE", "收盤價必須是有限正數", code=code, value=close))
        if strategy_date and row_date and row_date[:10] != strategy_date[:10]:
            issues.append(issue("ERROR", "STALE_ROW_DATE", "個股資料日與策略資料日不一致", code=code, rowDate=row_date, strategyDate=strategy_date))
        high, low, open_, volume = stock.get("high"), stock.get("low"), stock.get("open"), stock.get("volume")
        numeric_prices = [value for value in (open_, high, low, close) if value is not None]
        if any(not finite_number(value) or value <= 0 for value in numeric_prices):
            issues.append(issue("ERROR", "INVALID_OHLC", "OHLC 必須是有限正數", code=code))
        if finite_number(high) and finite_number(low) and high < low:
            issues.append(issue("ERROR", "HIGH_BELOW_LOW", "最高價低於最低價", code=code, high=high, low=low))
        if finite_number(high) and finite_number(close) and close > high:
            issues.append(issue("ERROR", "CLOSE_ABOVE_HIGH", "收盤價高於最高價", code=code, close=close, high=high))
        if finite_number(low) and finite_number(close) and close < low:
            issues.append(issue("ERROR", "CLOSE_BELOW_LOW", "收盤價低於最低價", code=code, close=close, low=low))
        if volume is not None and (not finite_number(volume) or volume < 0):
            issues.append(issue("ERROR", "INVALID_VOLUME", "成交量必須是有限非負數", code=code, value=volume))
        if stock.get("dataFresh") is False:
            issues.append(issue("ERROR", "STALE_STOCK", "個股被標示為非最新資料", code=code))

    expected_count = meta.get("totalAnalyzed") or meta.get("validCount") or len(stocks)
    if finite_number(expected_count) and int(expected_count) != len(stocks):
        issues.append(issue("ERROR", "COUNT_MISMATCH", "meta 筆數與 stocks 筆數不一致", expected=expected_count, actual=len(stocks)))
    if meta.get("freshnessStatus") and meta.get("freshnessStatus") != "COMPLETE":
        issues.append(issue("ERROR", "INCOMPLETE_FRESHNESS", "資料完整狀態不是 COMPLETE", value=meta.get("freshnessStatus")))
    if int(meta.get("staleCount") or 0) > 0:
        issues.append(issue("ERROR", "STALE_COUNT", "資料含過期個股", count=meta.get("staleCount")))
    upstream_errors = [str(value) for value in (meta.get("errors") or [])]
    if upstream_errors:
        joined = " ".join(upstream_errors).lower()
        if "timeout" in joined or "timed out" in joined or "逾時" in joined:
            issues.append(issue("WARNING", "API_TIMEOUT", "上游 API 發生 timeout", count=len(upstream_errors)))
        if "rate limit" in joined or "too many requests" in joined or "429" in joined:
            issues.append(issue("WARNING", "API_RATE_LIMIT", "上游 API 觸發 rate limit", count=len(upstream_errors)))
        issues.append(issue("WARNING", "UPSTREAM_ERRORS", "上游掃描器回報部分錯誤", count=len(upstream_errors)))

    total = len(stocks)
    completeness = round(complete / total * 100, 2) if total else 0.0
    return {
        "market": market,
        "status": dataset_status(issues),
        "updatedAt": meta.get("updatedAt") or meta.get("updatedAtTW"),
        "lastTradingDate": strategy_date or None,
        "completenessPct": completeness,
        "rowCount": total,
        "source": meta.get("officialSource") or meta.get("source") or "未標示",
        "apiStatus": "ERROR" if any(item["level"] == "ERROR" for item in issues) else "DEGRADED" if issues else "OK",
        "tradingDayCoverage": "CURRENT_SNAPSHOT_ONLY",
        "issues": issues,
    }


def validate_snapshots(payload: Any) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    if not isinstance(payload, dict):
        issues.append(issue("ERROR", "INVALID_SNAPSHOTS", "策略快照資料不是物件"))
        payload = {}
    for key, rows in payload.items():
        if not isinstance(rows, list):
            issues.append(issue("ERROR", "INVALID_SNAPSHOT_ROWS", "策略快照不是陣列", key=key))
            continue
        dates = [str(row.get("date") or "") for row in rows if isinstance(row, dict)]
        if len(rows) > 5:
            issues.append(issue("ERROR", "TOO_MANY_SNAPSHOTS", "單一股票超過 5 筆策略紀錄", key=key, count=len(rows)))
        duplicates = sorted({value for value in dates if value and dates.count(value) > 1})
        if duplicates:
            issues.append(issue("ERROR", "DUPLICATE_SNAPSHOT_DATE", "同日策略快照重複", key=key, dates=duplicates))
        valid_dates = [value for value in dates if parse_date(value)]
        if valid_dates != sorted(valid_dates, reverse=True):
            issues.append(issue("WARNING", "SNAPSHOT_ORDER", "策略快照不是由新到舊排列", key=key))
    return {"status": dataset_status(issues), "recordCount": len(payload), "issues": issues}


def validate_disposition(payload: dict[str, Any]) -> dict[str, Any]:
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    issues: list[dict[str, Any]] = []
    health = meta.get("sourceHealth") if isinstance(meta.get("sourceHealth"), dict) else {}
    failed = [source for source, ok in health.items() if not ok]
    if failed or meta.get("sourceErrors"):
        issues.append(issue("ERROR", "DISPOSITION_API_ERROR", "注意／處置官方資料來源失敗", sources=failed, errors=meta.get("sourceErrors") or {}))
    if not meta.get("updatedAt"):
        issues.append(issue("ERROR", "MISSING_DISPOSITION_TIME", "注意／處置資料缺少更新時間"))
    if find_non_finite(payload):
        issues.append(issue("ERROR", "NON_FINITE_DISPOSITION", "注意／處置資料含非有限數值"))
    return {
        "status": dataset_status(issues),
        "updatedAt": meta.get("updatedAt"),
        "recordCount": meta.get("recordCount"),
        "sourceHealth": health,
        "issues": issues,
    }


def dataset_status(issues: list[dict[str, Any]]) -> str:
    if any(item["level"] == "ERROR" for item in issues):
        return "ERROR"
    if any(item["level"] == "WARNING" for item in issues):
        return "WARNING"
    return "HEALTHY"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def build_health_report(root: Path) -> dict[str, Any]:
    data = root / "data"
    datasets: dict[str, Any] = {}
    load_issues: list[dict[str, Any]] = []
    required = {
        "台股": data / "latest.json",
        "美股": data / "us_latest.json",
        "注意處置": data / "disposition_risk.json",
        "策略快照": data / "strategy_snapshots.json",
    }
    loaded: dict[str, Any] = {}
    for name, path in required.items():
        try:
            loaded[name] = read_json(path)
        except FileNotFoundError:
            load_issues.append(issue("ERROR", "MISSING_FILE", "缺少必要資料檔", dataset=name, path=str(path.relative_to(root))))
        except (json.JSONDecodeError, UnicodeError) as exc:
            load_issues.append(issue("ERROR", "INVALID_JSON", "資料檔無法解析", dataset=name, error=str(exc)))

    if "台股" in loaded:
        datasets["twStocks"] = validate_stock_payload(loaded["台股"], "台股")
    if "美股" in loaded:
        datasets["usStocks"] = validate_stock_payload(loaded["美股"], "美股")
    if "注意處置" in loaded:
        datasets["disposition"] = validate_disposition(loaded["注意處置"])
    if "策略快照" in loaded:
        datasets["strategySnapshots"] = validate_snapshots(loaded["策略快照"])

    all_issues = load_issues + [item for dataset in datasets.values() for item in dataset.get("issues", [])]
    return {
        "meta": {
            "updatedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "status": dataset_status(all_issues),
            "errorCount": sum(item["level"] == "ERROR" for item in all_issues),
            "warningCount": sum(item["level"] == "WARNING" for item in all_issues),
            "policy": "只驗證來源資料，不猜測、不補值、不修改策略結果。",
        },
        "datasets": datasets,
        "issues": load_issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate stock data freshness and integrity without changing strategy output.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = build_health_report(args.root.resolve())
    output = args.output if args.output.is_absolute() else args.root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"data health: status={report['meta']['status']} "
        f"errors={report['meta']['errorCount']} warnings={report['meta']['warningCount']}"
    )
    return 1 if report["meta"]["errorCount"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
