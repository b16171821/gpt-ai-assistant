"""Independent Taiwan attention/disposition risk helpers.

This module deliberately does not import the stock scanner.  It turns official
TWSE/TPEX announcements into a separate trading-risk layer without changing
technical scores, rankings, or strategy prices.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence


NEW_RULE_EFFECTIVE_DATE = date(2026, 8, 10)
FINAL_STATUSES = {"DISPOSITION"}

TWSE_NOTICE_URL = "https://openapi.twse.com.tw/v1/announcement/notice"
TWSE_WARNING_URL = "https://openapi.twse.com.tw/v1/announcement/notetrans"
TWSE_DISPOSITION_URL = "https://openapi.twse.com.tw/v1/announcement/punish"
TWSE_HOLIDAY_URL = "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule"
TPEX_NOTICE_URL = "https://www.tpex.org.tw/openapi/v1/tpex_trading_warning_information"
TPEX_WARNING_URL = "https://www.tpex.org.tw/openapi/v1/tpex_trading_warning_note"
TPEX_DISPOSITION_URL = "https://www.tpex.org.tw/openapi/v1/tpex_disposal_information"


_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "兩": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CHINESE_UNITS = {"十": 10, "百": 100, "千": 1000}


def chinese_number(value: str) -> int:
    value = str(value or "").strip()
    if value.isdigit():
        return int(value)
    if value and all(char in _CHINESE_DIGITS for char in value):
        return int("".join(str(_CHINESE_DIGITS[char]) for char in value))
    total = 0
    current = 0
    for char in value:
        if char in _CHINESE_DIGITS:
            current = _CHINESE_DIGITS[char]
        elif char in _CHINESE_UNITS:
            total += (current or 1) * _CHINESE_UNITS[char]
            current = 0
    return total + current


def parse_official_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None

    compact = re.sub(r"\D", "", text)
    if len(compact) == 8 and compact.startswith("20"):
        try:
            return date(int(compact[:4]), int(compact[4:6]), int(compact[6:8]))
        except ValueError:
            return None
    if len(compact) == 7:
        try:
            return date(int(compact[:3]) + 1911, int(compact[3:5]), int(compact[5:7]))
        except ValueError:
            return None

    numeric = re.search(r"(?<!\d)(\d{3,4})[年/.-](\d{1,2})[月/.-](\d{1,2})日?", text)
    if numeric:
        year = int(numeric.group(1))
        if year < 1911:
            year += 1911
        try:
            return date(year, int(numeric.group(2)), int(numeric.group(3)))
        except ValueError:
            return None

    chinese = re.search(
        r"(?:民國)?([零〇一二兩三四五六七八九十百千]+)年"
        r"([零〇一二兩三四五六七八九十]+)月"
        r"([零〇一二兩三四五六七八九十]+)日",
        text,
    )
    if chinese:
        try:
            return date(
                chinese_number(chinese.group(1)) + 1911,
                chinese_number(chinese.group(2)),
                chinese_number(chinese.group(3)),
            )
        except ValueError:
            return None
    return None


def iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def parse_disposition_period(row: Mapping[str, Any]) -> tuple[date | None, date | None]:
    period = str(row.get("DispositionPeriod") or row.get("dispositionPeriod") or "")
    parts = re.split(r"[～~至]", period, maxsplit=1)
    start = parse_official_date(parts[0]) if parts else None
    end = parse_official_date(parts[1]) if len(parts) > 1 else None

    detail = str(
        row.get("Detail")
        or row.get("DisposalCondition")
        or row.get("detail")
        or ""
    )
    adjustment = re.search(r"修正其處置至(.{1,40}?)止", detail)
    adjusted_end = parse_official_date(adjustment.group(1)) if adjustment else None
    if adjusted_end:
        end = adjusted_end
    return start, end


def is_trading_day(day: date, holidays: Iterable[date] = ()) -> bool:
    return day.weekday() < 5 and day not in set(holidays)


def add_trading_days(start: date, trading_days: int, holidays: Iterable[date] = ()) -> date:
    if trading_days <= 0:
        return start
    holiday_set = set(holidays)
    cursor = start
    counted = 0
    while counted < trading_days:
        if is_trading_day(cursor, holiday_set):
            counted += 1
            if counted == trading_days:
                return cursor
        cursor += timedelta(days=1)
    return cursor


def next_trading_day(day: date, holidays: Iterable[date] = ()) -> date:
    holiday_set = set(holidays)
    cursor = day + timedelta(days=1)
    while not is_trading_day(cursor, holiday_set):
        cursor += timedelta(days=1)
    return cursor


def remaining_trading_days(
    as_of: date,
    start: date,
    end: date,
    holidays: Iterable[date] = (),
) -> int:
    if as_of > end:
        return 0
    holiday_set = set(holidays)
    cursor = max(as_of, start)
    remaining = 0
    while cursor <= end:
        if is_trading_day(cursor, holiday_set):
            remaining += 1
        cursor += timedelta(days=1)
    return remaining


def get_disposition_end_date(
    start: date,
    special_day_trading_case: bool = False,
    holidays: Iterable[date] = (),
) -> date:
    """Calculate the 2026-rule period only when an official end date is absent."""
    period_days = 7 if special_day_trading_case else 5
    return add_trading_days(start, period_days, holidays)


def extract_period_days(row: Mapping[str, Any]) -> int | None:
    detail = " ".join(
        str(row.get(field) or "")
        for field in ("Detail", "DisposalCondition", "DispositionPeriod")
    )
    matches = re.findall(r"([零〇一二兩三四五六七八九十\d]+)個營業日", detail)
    if not matches:
        return None
    return chinese_number(matches[-1])


def extract_match_interval(row: Mapping[str, Any]) -> str | None:
    detail = " ".join(
        str(row.get(field) or "")
        for field in ("Detail", "DisposalCondition", "TradingMethod", "MatchInterval")
    )
    matches = re.findall(r"約?每([零〇一二兩三四五六七八九十百\d]+)分鐘", detail)
    if not matches:
        return None
    minutes = chinese_number(matches[-1])
    return f"約每 {minutes} 分鐘" if minutes else None


def parse_watch_count(row: Mapping[str, Any]) -> int:
    explicit = row.get("NumberOfAnnouncement") or row.get("watchCount")
    if explicit is not None and str(explicit).strip().isdigit():
        return int(str(explicit).strip())
    text = " ".join(
        str(row.get(field) or "")
        for field in ("AccumulationSituation", "TradingInformation", "Detail")
    )
    numbers = [
        chinese_number(value)
        for value in re.findall(r"([零〇一二兩三四五六七八九十百\d]+)次", text)
    ]
    return max(numbers, default=0)


def row_code(row: Mapping[str, Any]) -> str:
    return str(
        row.get("Code")
        or row.get("SecuritiesCompanyCode")
        or row.get("stockCode")
        or ""
    ).strip()


def row_name(row: Mapping[str, Any]) -> str:
    return str(row.get("Name") or row.get("CompanyName") or row.get("stockName") or "").strip()


def row_date(row: Mapping[str, Any]) -> date | None:
    return parse_official_date(row.get("Date") or row.get("date"))


def official_reason(row: Mapping[str, Any]) -> str | None:
    value = (
        row.get("ReasonsOfDisposition")
        or row.get("DispositionReasons")
        or row.get("TradingInformation")
        or row.get("AccumulationSituation")
    )
    return str(value).strip() if value else None


def _base_risk(updated_at: str) -> dict[str, Any]:
    return {
        "status": "NORMAL",
        "riskLevel": 0,
        "watchCount": 0,
        "lastWatchDate": None,
        "watchReason": None,
        "nearDisposition": False,
        "remainingToDisposition": None,
        "startDate": None,
        "endDate": None,
        "remainingTradingDays": None,
        "expectedReleaseDate": None,
        "reason": None,
        "matchInterval": None,
        "prepaymentRequired": None,
        "restrictionSummary": None,
        "periodBusinessDays": None,
        "isUpcoming": False,
        "tradeRecommendation": "可依原策略評估，仍須遵守買入觀察區與停損。",
        "eligibleForNormalEntry": True,
        "source": None,
        "sourceUrl": None,
        "updatedAt": updated_at,
        "dataAvailable": True,
    }


def calculate_disposition_risk(
    *,
    as_of: date,
    updated_at: str,
    holidays: Iterable[date] = (),
    watch_row: Mapping[str, Any] | None = None,
    warning_row: Mapping[str, Any] | None = None,
    disposition_row: Mapping[str, Any] | None = None,
    source_name: str | None = None,
    source_url: str | None = None,
    source_available: bool = True,
) -> dict[str, Any]:
    risk = _base_risk(updated_at)
    risk["source"] = source_name
    risk["sourceUrl"] = source_url

    if not source_available:
        risk.update(
            status="UNKNOWN",
            riskLevel=None,
            dataAvailable=False,
            eligibleForNormalEntry=False,
            tradeRecommendation="處置資料暫時無法取得，請先查官方公告，不依未知狀態追價。",
        )
        return risk

    if disposition_row:
        start, end = parse_disposition_period(disposition_row)
        period_days = extract_period_days(disposition_row)
        if start and not end and period_days in (5, 7):
            end = add_trading_days(start, period_days, holidays)
        if start and end and end >= as_of:
            detail = str(
                disposition_row.get("Detail")
                or disposition_row.get("DisposalCondition")
                or ""
            )
            upcoming = as_of < start
            prepayment = bool(
                re.search(r"預收|收取全部之?買進價金|收取全部買進價金", detail)
            )
            risk.update(
                status="DISPOSITION",
                riskLevel=3,
                startDate=iso(start),
                endDate=iso(end),
                remainingTradingDays=remaining_trading_days(as_of, start, end, holidays),
                expectedReleaseDate=iso(next_trading_day(end, holidays)),
                reason=official_reason(disposition_row),
                matchInterval=extract_match_interval(disposition_row),
                prepaymentRequired=prepayment,
                restrictionSummary=(
                    "官方公告含預收買進價金或賣出證券限制。"
                    if prepayment
                    else "依官方公告之處置措施辦理。"
                ),
                periodBusinessDays=period_days,
                isUpcoming=upcoming,
                eligibleForNormalEntry=False,
                tradeRecommendation=(
                    "已公告處置，等待處置期結束，不列入正常進場推薦。"
                    if upcoming
                    else "策略訊號存在，但因處置中，暫不列入正常進場推薦。"
                ),
            )
            return risk

    if warning_row:
        risk.update(
            status="WARNING",
            riskLevel=2,
            watchCount=parse_watch_count(warning_row),
            lastWatchDate=iso(row_date(warning_row)),
            watchReason=official_reason(warning_row),
            nearDisposition=True,
            remainingToDisposition=1,
            eligibleForNormalEntry=False,
            tradeRecommendation="官方列入處置預警，避免追價，等待風險解除。",
        )
        return risk

    if watch_row:
        risk.update(
            status="WATCH",
            riskLevel=1,
            watchCount=parse_watch_count(watch_row),
            lastWatchDate=iso(row_date(watch_row)),
            watchReason=official_reason(watch_row),
            nearDisposition=False,
            eligibleForNormalEntry=False,
            tradeRecommendation="注意股交易風險提高；保留原策略價位，但不列入正常進場推薦。",
        )
    return risk


def latest_rows_by_code(rows: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        code = row_code(row)
        if not code:
            continue
        current = result.get(code)
        if current is None or (row_date(row) or date.min) >= (row_date(current) or date.min):
            result[code] = row
    return result


def current_dispositions_by_code(
    rows: Sequence[Mapping[str, Any]], as_of: date
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        code = row_code(row)
        _, end = parse_disposition_period(row)
        if not code or not end or end < as_of:
            continue
        current = result.get(code)
        current_start, _ = parse_disposition_period(current or {})
        start, _ = parse_disposition_period(row)
        if current is None or (start or date.min) >= (current_start or date.min):
            result[code] = row
    return result


def holiday_dates(rows: Sequence[Mapping[str, Any]]) -> set[date]:
    holidays: set[date] = set()
    for row in rows:
        description = " ".join(str(value or "") for value in row.values())
        if not re.search(r"休市|放假", description) or re.search(r"開始交易|開始買賣", description):
            continue
        parsed = None
        for value in row.values():
            parsed = parse_official_date(value)
            if parsed:
                break
        if parsed:
            holidays.add(parsed)
    return holidays
