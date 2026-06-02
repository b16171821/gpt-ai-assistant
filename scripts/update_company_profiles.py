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
MAX_PER_MARKET = 80


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


def short(text, max_len=160):
    text = " ".join(str(text or "").replace("\n", " ").split())
    return text[:max_len]


def ticker_for(market, row):
    if market == "美股":
        return row.get("ticker")
    code = row.get("code")
    if not code:
        return None
    market_name = row.get("market", "")
    if "上櫃" in market_name or str(row.get("ticker", "")).endswith(".TWO"):
        return f"{code}.TWO"
    return f"{code}.TW"


def fallback_profile(market, row):
    name = row.get("name") or row.get("ticker") or row.get("code") or "該公司"
    pattern = row.get("pattern") or "主升段型態"
    reason = row.get("reason") or "量價條件符合觀察"
    market_name = row.get("market") or market
    return {
        "industry": row.get("industry") or market_name,
        "sector": row.get("sector") or market,
        "companyProfile": short(f"{name}目前以技術型態、量能與位置作為觀察重點。"),
        "theme": short(f"型態：{pattern}｜原因：{reason}"),
        "recentInfo": "重大資訊待補，請以公開資訊觀測站、公司公告或券商資訊為準。",
        "profileSource": "scanner fallback",
        "profileUpdatedAt": now_tw(),
    }


def fetch_profile(market, row):
    base = fallback_profile(market, row)
    ticker = ticker_for(market, row)
    if not ticker:
        return base
    try:
        info = yf.Ticker(ticker).info or {}
        industry = info.get("industry") or base["industry"]
        sector = info.get("sector") or base["sector"]
        summary = info.get("longBusinessSummary") or base["companyProfile"]
        return {
            **base,
            "industry": industry,
            "sector": sector,
            "companyProfile": short(summary, 180),
            "theme": short(f"產業：{industry}｜板塊：{sector}｜型態：{row.get('pattern') or '-'}｜原因：{row.get('reason') or '-'}", 180),
            "profileSource": "Yahoo Finance / yfinance",
            "profileUpdatedAt": now_tw(),
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
    candidates = [s for s in stocks if float(s.get("score") or 0) >= 3]
    candidates = sorted(candidates, key=rank_score, reverse=True)[:MAX_PER_MARKET]

    for row in candidates:
        k = key_for(market, row)
        if k not in profiles:
            profiles[k] = fetch_profile(market, row)

    output = []
    for row in stocks:
        k = key_for(market, row)
        profile = profiles.get(k)
        if profile:
            row.update(profile)
        else:
            row.update(fallback_profile(market, row))
        output.append(row)

    payload["stocks"] = output
    meta = payload.get("meta", {})
    meta["profileEnriched"] = True
    meta["profileUpdatedAt"] = now_tw()
    payload["meta"] = meta
    return payload, profiles


def main():
    DATA_DIR.mkdir(exist_ok=True)
    cache = read_json(PROFILE_PATH, {"profiles": {}})
    profiles = cache.get("profiles", {}) if isinstance(cache, dict) else {}

    if TW_PATH.exists():
        tw = read_json(TW_PATH, {"meta": {}, "stocks": []})
        tw, profiles = process_market("台股", tw, profiles)
        write_json(TW_PATH, tw)
        print(f"enriched 台股: {len(tw.get('stocks', []))}")

    if US_PATH.exists():
        us = read_json(US_PATH, {"meta": {}, "stocks": []})
        us, profiles = process_market("美股", us, profiles)
        write_json(US_PATH, us)
        print(f"enriched 美股: {len(us.get('stocks', []))}")

    write_json(PROFILE_PATH, {"updatedAt": now_tw(), "profiles": profiles})
    print(f"company profiles cached: {len(profiles)}")


if __name__ == "__main__":
    main()
