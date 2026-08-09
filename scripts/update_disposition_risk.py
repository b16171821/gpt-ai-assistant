"""Build data/disposition_risk.json from official TWSE and TPEX OpenAPI data."""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from disposition_service import (
    TPEX_DISPOSITION_URL,
    TPEX_NOTICE_URL,
    TPEX_WARNING_URL,
    TWSE_DISPOSITION_URL,
    TWSE_HOLIDAY_URL,
    TWSE_NOTICE_URL,
    TWSE_WARNING_URL,
    calculate_disposition_risk,
    current_dispositions_by_code,
    holiday_dates,
    latest_rows_by_code,
)


ROOT = Path(__file__).resolve().parents[1]
LATEST_PATH = ROOT / "data" / "latest.json"
OUTPUT_PATH = ROOT / "data" / "disposition_risk.json"
TAIPEI = timezone(timedelta(hours=8), name="Asia/Taipei")

SOURCES = {
    "twseNotice": TWSE_NOTICE_URL,
    "twseWarning": TWSE_WARNING_URL,
    "twseDisposition": TWSE_DISPOSITION_URL,
    "twseHoliday": TWSE_HOLIDAY_URL,
    "tpexNotice": TPEX_NOTICE_URL,
    "tpexWarning": TPEX_WARNING_URL,
    "tpexDisposition": TPEX_DISPOSITION_URL,
}


def fetch_json(url: str, attempts: int = 3) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "gpt-ai-assistant-disposition-radar/1.0",
        },
    )
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                return json.loads(response.read().decode("utf-8-sig"))
        except Exception as exc:  # Network resilience is intentional here.
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(1 + attempt)
    raise RuntimeError(str(last_error or "unknown fetch error"))


def rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        value = payload.get("value") or payload.get("data") or []
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
    return []


def load_sources() -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    payloads: dict[str, list[dict[str, Any]]] = {}
    errors: dict[str, str] = {}
    for name, url in SOURCES.items():
        try:
            payloads[name] = rows(fetch_json(url))
        except Exception as exc:
            payloads[name] = []
            errors[name] = str(exc)
    return payloads, errors


def source_health(errors: dict[str, str], prefix: str) -> bool:
    required = [f"{prefix}Notice", f"{prefix}Warning", f"{prefix}Disposition"]
    return not any(name in errors for name in required)


def build_payload(
    stock_payload: dict[str, Any],
    official: dict[str, list[dict[str, Any]]],
    errors: dict[str, str],
    now: datetime,
) -> dict[str, Any]:
    as_of = now.date()
    updated_at = now.isoformat(timespec="seconds")
    holidays = holiday_dates(official.get("twseHoliday", []))
    maps = {
        "twseWatch": latest_rows_by_code(official.get("twseNotice", [])),
        "twseWarning": latest_rows_by_code(official.get("twseWarning", [])),
        "twseDisposition": current_dispositions_by_code(
            official.get("twseDisposition", []), as_of
        ),
        "tpexWatch": latest_rows_by_code(official.get("tpexNotice", [])),
        "tpexWarning": latest_rows_by_code(official.get("tpexWarning", [])),
        "tpexDisposition": current_dispositions_by_code(
            official.get("tpexDisposition", []), as_of
        ),
    }
    exchange_health = {
        "TWSE": source_health(errors, "twse"),
        "TPEX": source_health(errors, "tpex"),
    }
    records: dict[str, Any] = {}
    normal_codes: list[str] = []
    counts = {status: 0 for status in ("NORMAL", "WATCH", "WARNING", "DISPOSITION", "UNKNOWN")}

    for stock in stock_payload.get("stocks", []):
        if not isinstance(stock, dict):
            continue
        code = str(stock.get("code") or "").strip()
        if not code:
            continue
        exchange = "TPEX" if str(stock.get("market") or "").strip() == "上櫃" else "TWSE"
        prefix = "tpex" if exchange == "TPEX" else "twse"
        source_name = "櫃買中心" if exchange == "TPEX" else "台灣證券交易所"
        disposition_row = maps[f"{prefix}Disposition"].get(code)
        warning_row = maps[f"{prefix}Warning"].get(code)
        watch_row = maps[f"{prefix}Watch"].get(code)
        source_kind = "Disposition" if disposition_row else "Warning" if warning_row else "Notice"
        risk = calculate_disposition_risk(
            as_of=as_of,
            updated_at=updated_at,
            holidays=holidays,
            watch_row=watch_row,
            warning_row=warning_row,
            disposition_row=disposition_row,
            source_name=source_name,
            source_url=SOURCES[f"{prefix}{source_kind}"],
            source_available=exchange_health[exchange],
        )
        counts[risk["status"]] = counts.get(risk["status"], 0) + 1
        if risk["status"] == "NORMAL":
            normal_codes.append(code)
        else:
            records[f"台股:{code}"] = {
                "stockCode": code,
                "stockName": str(stock.get("name") or "").strip(),
                "exchange": exchange,
                "dispositionRisk": risk,
            }

    strategy_date = (
        stock_payload.get("meta", {}).get("officialDataDate")
        or stock_payload.get("meta", {}).get("strategyAsOfDate")
    )
    return {
        "meta": {
            "schemaVersion": 1,
            "updatedAt": updated_at,
            "evaluatedDate": as_of.isoformat(),
            "strategyDataDate": strategy_date,
            "recordCount": len(records) + len(normal_codes),
            "counts": counts,
            "sourceHealth": exchange_health,
            "sourceErrors": errors,
            "sources": [
                {"name": "台灣證券交易所", "urls": [SOURCES[name] for name in SOURCES if name.startswith("twse")]},
                {"name": "櫃買中心", "urls": [SOURCES[name] for name in SOURCES if name.startswith("tpex")]},
            ],
            "ruleEffectiveDate": "2026-08-10",
            "generalDispositionBusinessDays": 5,
            "specialDispositionBusinessDays": 7,
            "note": "正式狀態以官方公告為準；資料缺漏時標示 UNKNOWN，不自行推測。",
        },
        "normalCodes": sorted(normal_codes),
        "records": records,
    }


def main() -> int:
    if not LATEST_PATH.exists():
        print(f"missing input: {LATEST_PATH}", file=sys.stderr)
        return 1
    stock_payload = json.loads(LATEST_PATH.read_text(encoding="utf-8-sig"))
    official, errors = load_sources()
    payload = build_payload(stock_payload, official, errors, datetime.now(TAIPEI))
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print(
        "disposition risk updated: "
        f"records={payload['meta']['recordCount']} counts={payload['meta']['counts']} "
        f"errors={len(errors)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
