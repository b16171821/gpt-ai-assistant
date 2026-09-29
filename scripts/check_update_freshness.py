"""Read-only publication gate against TWSE's actual completed sessions."""

import argparse
import json
import math
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
TW = timezone(timedelta(hours=8))
SOURCE = "https://www.twse.com.tw/indicesReport/MI_5MINS_HIST"


def request_json(url):
    request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except (OSError, ValueError):
            if attempt == 2:
                raise RuntimeError("TWSE official session data unavailable") from None
            time.sleep(2 ** attempt)


def session_dates(payload):
    if not isinstance(payload, dict):
        raise ValueError("Invalid official session payload")
    if payload.get("stat") == "很抱歉，沒有符合條件的資料!":
        return []
    fields = payload.get("fields", [])
    if payload.get("stat") != "OK" or "日期" not in fields or "收盤指數" not in fields:
        raise ValueError("Invalid official session fields")
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise ValueError("Invalid official session rows")
    dates = []
    for row in rows:
        year, month, day = map(int, row[fields.index("日期")].split("/"))
        close = float(str(row[fields.index("收盤指數")]).replace(",", ""))
        if not math.isfinite(close) or close <= 0:
            raise ValueError("Invalid official closing index")
        dates.append(date(year + 1911 if year < 1911 else year, month, day))
    return dates


def latest_completed_session(now=None, requester=request_json):
    now = (now or datetime.now(TW)).astimezone(TW)
    month_start = now.date().replace(day=1)
    previous_month = (month_start - timedelta(days=1)).replace(day=1)
    # Actual published sessions, not a weekday approximation, handle holidays
    # and emergency closures. Current-day bars are ignored before market close.
    cutoff = now.date() if (now.hour, now.minute) >= (13, 35) else now.date() - timedelta(days=1)
    dates = []
    for month in (month_start, previous_month):
        payload = requester(f"{SOURCE}?date={month:%Y%m%d}&response=json")
        dates.extend(d for d in session_dates(payload) if d <= cutoff)
    if not dates:
        raise ValueError("No confirmed official trading session available")
    return max(dates).isoformat()


def assess_payload(payload, expected_date):
    date.fromisoformat(expected_date)
    payload = payload if isinstance(payload, dict) else {}
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    dates = [meta.get(k) for k in ("officialDataDate", "strategyAsOfDate")]
    stocks = payload.get("stocks") if isinstance(payload.get("stocks"), list) else []
    current = bool(stocks) and meta.get("freshnessStatus") == "COMPLETE"
    current = current and all(d == expected_date for d in dates)
    current = current and all(
        isinstance(row, dict)
        and (row.get("officialDataDate") or row.get("strategyAsOfDate") or row.get("date")) == expected_date
        for row in stocks
    )
    return {"needsUpdate": not current, "expectedDate": expected_date,
            "actualDate": meta.get("officialDataDate") or meta.get("strategyAsOfDate")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=ROOT / "data/latest.json")
    parser.add_argument("--check-only", action="store_true", help="Report whether a catch-up run is needed")
    args = parser.parse_args()
    try:
        expected = latest_completed_session()
        try:
            payload = json.loads(args.path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            payload = {}
        result = assess_payload(payload, expected)
        print(json.dumps(result, ensure_ascii=False))
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
                output.write(f"needs_update={str(result['needsUpdate']).lower()}\n")
        if result["needsUpdate"] and not args.check_only:
            print("::error title=收盤資料尚未更新::資料與官方最新收盤日不一致，停止發布，保留上次正式資料。")
            return 1
        return 0
    except (RuntimeError, ValueError, TypeError, KeyError, IndexError) as exc:
        print(f"::error title=官方資料日期驗證失敗::{type(exc).__name__}: 無法確認最新收盤日，不推測日期。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
