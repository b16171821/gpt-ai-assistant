import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
TW_PATH = DATA_DIR / "latest.json"
US_PATH = DATA_DIR / "us_latest.json"
PROFILE_PATH = DATA_DIR / "company_profiles.json"
TAIPEI_TZ = timezone(timedelta(hours=8))
MAX_PER_MARKET = 30


def now_tw():
    return datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")


def read_json(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def key_for(market, row):
    code = row.get("code") or row.get("ticker") or row.get("name")
    return f"{market}:{code}"


def yfinance_ticker(market, row):
    if market == "美股":
        return row.get("ticker")
    code = row.get("code")
    market_name = row.get("market", "")
    if not code:
        return None
    if "上櫃" in market_name or row.get("ticker", "").endswith(".TWO"):
        return f"{code}.TWO"
    return f"{code}.TW"


def fallback_profile(market, row):
    name = row.get("name") or row.get("ticker") or row.get("code") or "該公司"
    pattern = row.get("pattern") or "主升段型態"
    reason = row.get("reason") or "量價條件符合觀察"
    theme = row.get("theme") or "尚未取得公開題材資料"
    return {
        "market": market,
        "code": row.get("code") or row.get("ticker"),
        "name": name,
        "industry": row.get("industry") or "尚未取得產業分類",
        "sector": row.get("sector") or "尚未取得產業分類",
        "businessSummary": f"{name}：{theme}。目前系統主要依據型態、量能與位置觀察。",
        "themeSummary": f"型態：{pattern}｜原因：{reason}",
        "newsSummary": "尚未取得最近重大資訊，請以公開資訊觀測站、公司公告或券商新聞為準。",
        "source": "scanner fallback",
        "updatedAt": now_tw(),
    }


def fetch_profile(market, row):
    base = fallback_profile(market, row)
    ticker = yfinance_ticker(market, row)
    if not ticker:
        return base
    try:
        info = yf.Ticker(ticker).info or {}
        industry = info.get("industry") or base["industry"]
        sector = info.get("sector") or base["sector"]
        summary = info.get("longBusinessSummary") or info.get("businessSummary") or base["businessSummary"]
        news_items = []
        try:
            for item in (yf.Ticker(ticker).news or [])[:3]:
                title = item.get("title")
                if title:
                    news_items.append(title)
        except Exception:
            pass
        news_summary = "；".join(news_items) if news_items else base["newsSummary"]
        theme_summary = f"產業：{industry}｜題材：{sector}｜型態：{row.get('pattern') or '-'}｜原因：{row.get('reason') or '-'}"
        return {
            **base,
            "industry": industry,
            "sector": sector,
            "businessSummary": str(summary).strip()[:260],
            "themeSummary": theme_summary,
            "newsSummary": news_summary,
            "source": "Yahoo Finance / yfinance",
            "updatedAt": now_tw(),
        }
    except Exception:
        return base


def rank_score(row):
    try:
        score = float(row.get("score") or 0)
    except Exception:
        score = 0
    strict = 50 if row.get("strictOk") else 0
    win = 30 if row.get("winRate") == "高" else 15 if row.get("winRate") == "中" else 0
    try:
        rise = float(row.get("rise60") or 0) / 10
    except Exception:
        rise = 0
    return score * 100 + strict + win + rise


def process_market(market, payload, profiles):
    stocks = payload.get("stocks", []) if isinstance(payload, dict) else []
    stocks = [s for s in stocks if float(s.get("score") or 0) >= 3]
    stocks = sorted(stocks, key=rank_score, reverse=True)[:MAX_PER_MARKET]
    for row in stocks:
        k = key_for(market, row)
        old = profiles.get(k)
        # 每天更新一次；已經有資料也會重新整理，避免題材太舊。
        profiles[k] = fetch_profile(market, row)
    return profiles


def main():
    DATA_DIR.mkdir(exist_ok=True)
    payload = read_json(PROFILE_PATH, {"profiles": {}})
    profiles = payload.get("profiles", {}) if isinstance(payload, dict) else {}
    tw = read_json(TW_PATH, {"stocks": []})
    us = read_json(US_PATH, {"stocks": []})
    profiles = process_market("台股", tw, profiles)
    profiles = process_market("美股", us, profiles)
    write_json(PROFILE_PATH, {"updatedAt": now_tw(), "profiles": profiles})
    print(f"company profiles updated: {len(profiles)}")


if __name__ == "__main__":
    main()
