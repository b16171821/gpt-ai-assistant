import json
import math
import re
import time
from collections import Counter
from datetime import date, datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd


TWSE_DAILY_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
TWSE_INDEX_URL = "https://www.twse.com.tw/indicesReport/MI_5MINS_HIST"
TPEX_DAILY_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
TPEX_INDEX_URL = "https://www.tpex.org.tw/www/zh-tw/indexInfo/inx"
DEFAULT_TIMEOUT = 45


class OfficialDataError(RuntimeError):
    pass


def normalize_tw_date(value):
    text = re.sub(r"\D", "", str(value or ""))
    if len(text) == 7:
        year = int(text[:3]) + 1911
        return f"{year:04d}-{text[3:5]}-{text[5:7]}"
    if len(text) == 8:
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return ""


def parse_number(value, default=0.0):
    text = str(value or "").strip().replace(",", "")
    if not text or text in {"--", "---", "除權", "除息"}:
        return default
    text = re.sub(r"^[Xx]", "", text)
    try:
        number = float(text)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def request_json(url, params=None, form=None, timeout=DEFAULT_TIMEOUT):
    if params:
        url = f"{url}?{urlencode(params)}"
    body = urlencode(form).encode("utf-8") if form is not None else None
    request = Request(
        url,
        data=body,
        headers={
            "Accept": "application/json,text/plain,*/*",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "User-Agent": "Mozilla/5.0 (compatible; gpt-ai-assistant/1.0)",
        },
    )
    last_error = None
    for attempt in range(3):
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8-sig"))
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise OfficialDataError(f"官方資料請求失敗：{url}（{last_error}）")


def _quote(code, quote_date, open_, high, low, close, volume, market):
    return {
        "code": str(code).strip(),
        "date": quote_date,
        "open": parse_number(open_),
        "high": parse_number(high),
        "low": parse_number(low),
        "close": parse_number(close),
        "volume": int(parse_number(volume)),
        "market": market,
    }


def fetch_tpex_daily_quotes(requester=request_json):
    payload = requester(TPEX_DAILY_URL)
    if not isinstance(payload, list):
        raise OfficialDataError("櫃買中心收盤資料格式不正確")

    parsed = []
    for row in payload:
        code = str(row.get("SecuritiesCompanyCode", "")).strip()
        quote_date = normalize_tw_date(row.get("Date"))
        item = _quote(
            code,
            quote_date,
            row.get("Open"),
            row.get("High"),
            row.get("Low"),
            row.get("Close"),
            row.get("TradingShares"),
            "上櫃",
        )
        if re.fullmatch(r"\d{4}", code) and quote_date and item["close"] > 0:
            parsed.append(item)

    if not parsed:
        raise OfficialDataError("櫃買中心沒有可用的正式收盤資料")
    date_counts = Counter(item["date"] for item in parsed)
    official_date = max(date_counts, key=lambda key: (date_counts[key], key))
    quotes = {item["code"]: item for item in parsed if item["date"] == official_date}
    return official_date, quotes


def _find_twse_quote_table(payload):
    for table in payload.get("tables", []):
        fields = table.get("fields") or []
        required = {"證券代號", "開盤價", "最高價", "最低價", "收盤價", "成交股數"}
        if required.issubset(set(fields)):
            return table
    return None


def fetch_twse_daily_quotes(official_date, requester=request_json):
    payload = requester(
        TWSE_DAILY_URL,
        params={"date": official_date.replace("-", ""), "type": "ALLBUT0999", "response": "json"},
    )
    table = _find_twse_quote_table(payload)
    if not table:
        raise OfficialDataError(f"證交所 {official_date} 收盤資料不存在或格式不正確")

    fields = table["fields"]
    indexes = {name: fields.index(name) for name in ("證券代號", "成交股數", "開盤價", "最高價", "最低價", "收盤價")}
    quotes = {}
    for row in table.get("data") or []:
        code = str(row[indexes["證券代號"]]).strip()
        item = _quote(
            code,
            official_date,
            row[indexes["開盤價"]],
            row[indexes["最高價"]],
            row[indexes["最低價"]],
            row[indexes["收盤價"]],
            row[indexes["成交股數"]],
            "上市",
        )
        if re.fullmatch(r"\d{4}", code) and item["close"] > 0:
            quotes[code] = item
    if not quotes:
        raise OfficialDataError(f"證交所 {official_date} 沒有可用的正式收盤資料")
    return quotes


def load_latest_official_quotes(requester=request_json):
    official_date, tpex_quotes = fetch_tpex_daily_quotes(requester=requester)
    twse_quotes = fetch_twse_daily_quotes(official_date, requester=requester)
    quotes = dict(twse_quotes)
    quotes.update(tpex_quotes)
    return {
        "officialDate": official_date,
        "quotes": quotes,
        "listedCount": len(twse_quotes),
        "otcCount": len(tpex_quotes),
        "officialQuoteCount": len(quotes),
        "source": "臺灣證券交易所與證券櫃檯買賣中心正式收盤資料",
    }


def validate_official_coverage(expected_codes, quotes, minimum_pct=95):
    expected = {str(code) for code in expected_codes if str(code)}
    covered = expected.intersection(quotes)
    total = len(expected)
    coverage_pct = len(covered) / total * 100 if total else 0
    result = {
        "expectedCount": total,
        "coveredCount": len(covered),
        "missingCount": max(0, total - len(covered)),
        "coveragePct": round(coverage_pct, 2),
        "missingCodes": sorted(expected - covered),
    }
    if not total or coverage_pct < minimum_pct:
        raise OfficialDataError(
            f"官方收盤覆蓋率不足：{len(covered)}/{total}（{coverage_pct:.2f}%），"
            f"低於 {minimum_pct:.2f}% 門檻"
        )
    return result


def merge_official_bar(df, quote):
    if df is None or getattr(df, "empty", True):
        return df
    result = df.copy()
    result.index = pd.to_datetime(result.index).tz_localize(None).normalize()
    target = pd.Timestamp(quote["date"])
    values = {
        "Open": quote["open"],
        "High": quote["high"],
        "Low": quote["low"],
        "Close": quote["close"],
        "Volume": quote["volume"],
    }
    if "Adj Close" in result.columns:
        values["Adj Close"] = quote["close"]
    for column, value in values.items():
        if column not in result.columns:
            result[column] = pd.NA
        result.loc[target, column] = value
    return result[~result.index.duplicated(keep="last")].sort_index()


def _month_starts(as_of_date, count):
    current = date.fromisoformat(as_of_date).replace(day=1)
    output = []
    year, month = current.year, current.month
    for _ in range(count):
        output.append(date(year, month, 1))
        month -= 1
        if month == 0:
            year -= 1
            month = 12
    return output


def _index_rows_from_table(table):
    fields = table.get("fields") or []
    aliases = {
        "date": ("日期",),
        "open": ("開盤指數", "開市"),
        "high": ("最高指數", "最高"),
        "low": ("最低指數", "最低"),
        "close": ("收盤指數", "收市"),
    }
    indexes = {}
    for key, choices in aliases.items():
        indexes[key] = next((fields.index(name) for name in choices if name in fields), None)
    if any(index is None for index in indexes.values()):
        return []
    rows = []
    for raw in table.get("data") or []:
        row_date = normalize_tw_date(raw[indexes["date"]])
        close = parse_number(raw[indexes["close"]])
        if row_date and close > 0:
            rows.append(
                {
                    "Date": row_date,
                    "Open": parse_number(raw[indexes["open"]], close),
                    "High": parse_number(raw[indexes["high"]], close),
                    "Low": parse_number(raw[indexes["low"]], close),
                    "Close": close,
                    "Volume": 0,
                }
            )
    return rows


def fetch_twse_index_history(as_of_date, requester=request_json, months=3):
    rows = []
    for month in _month_starts(as_of_date, months):
        payload = requester(
            TWSE_INDEX_URL,
            params={"date": month.strftime("%Y%m%d"), "response": "json"},
        )
        rows.extend(_index_rows_from_table({"fields": payload.get("fields"), "data": payload.get("data")}))
    return _index_frame(rows, as_of_date, "加權指數")


def fetch_tpex_index_history(as_of_date, requester=request_json, months=3):
    rows = []
    for month in _month_starts(as_of_date, months):
        payload = requester(
            TPEX_INDEX_URL,
            params={"date": month.strftime("%Y/%m/%d"), "response": "json"},
        )
        tables = payload.get("tables") or []
        if tables:
            rows.extend(_index_rows_from_table(tables[0]))
    return _index_frame(rows, as_of_date, "櫃買指數")


def _index_frame(rows, as_of_date, name):
    if not rows:
        raise OfficialDataError(f"{name}沒有可用的官方歷史資料")
    frame = pd.DataFrame(rows)
    frame["Date"] = pd.to_datetime(frame["Date"])
    frame = frame.drop_duplicates(subset=["Date"], keep="last").sort_values("Date").set_index("Date")
    frame = frame.loc[frame.index <= pd.Timestamp(as_of_date)]
    if len(frame) < 30 or str(frame.index[-1].date()) != as_of_date:
        latest = str(frame.index[-1].date()) if len(frame) else "無"
        raise OfficialDataError(f"{name}資料未更新至 {as_of_date}（最新：{latest}，筆數：{len(frame)}）")
    return frame
