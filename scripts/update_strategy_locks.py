import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
LOCK_PATH = DATA_DIR / "strategy_locks.json"
TW_PATH = DATA_DIR / "latest.json"
US_PATH = DATA_DIR / "us_latest.json"
TAIPEI_TZ = timezone(timedelta(hours=8))
MAX_LOCKS_PER_MARKET = 10


def now_tw():
    return datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")


def n(v, default=0.0):
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default


def read_json(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def stock_key(market, row):
    return f"{market}:{row.get('code') or row.get('ticker') or row.get('name')}"


def rank_score(row):
    score = n(row.get("score"))
    strict = 50 if row.get("strictOk") else 0
    win = 30 if row.get("winRate") == "高" else 15 if row.get("winRate") == "中" else 0
    rise = n(row.get("rise60")) / 10
    return score * 100 + strict + win + rise


def current_status(row, lock):
    close = n(row.get("close"))
    original_target = n(lock.get("originalTarget"))
    original_stop = n(lock.get("originalStopLoss"))
    current_target = n(row.get("target"))
    current_stop = n(row.get("stopLoss"))

    extension_target = None
    if current_target and original_target and current_target > original_target:
        extension_target = round(current_target, 2)

    if original_stop and close <= original_stop:
        status = "策略結束"
        holder = "跌破原始停損，依紀律處理。"
        empty = "取消觀察，等待新結構。"
    elif original_target and close >= original_target:
        status = "已達第一目標"
        holder = "已達原始第一目標，建議先分批停利 30%～50%；剩餘部位才看延伸目標。"
        empty = "不追高，等新的整理區或回踩不破。"
    elif original_target and close >= original_target * 0.95:
        status = "接近第一目標"
        holder = "接近原始第一目標，準備分批停利。"
        empty = "不追高，等回踩。"
    else:
        status = "策略追蹤中"
        holder = "未達第一目標前，守風險區續抱觀察。"
        empty = "空手不追高，等突破確認或回踩不破。"

    return {
        "trackingStatus": status,
        "currentPrice": round(close, 2),
        "currentTarget": round(current_target, 2) if current_target else None,
        "currentStopLoss": round(current_stop, 2) if current_stop else None,
        "extensionTarget": extension_target,
        "holderAction": holder,
        "emptyAction": empty,
        "updatedAt": now_tw(),
    }


def create_lock(market, row):
    key = stock_key(market, row)
    code = row.get("code") or row.get("ticker")
    name = row.get("name") or code
    entry_low = n(row.get("chaseRangeLow") or row.get("entryPullback") or row.get("neckline"))
    entry_high = n(row.get("chaseRangeHigh") or row.get("entryBreakout") or row.get("neckline"))
    target = n(row.get("target"))
    stop = n(row.get("stopLoss"))
    created = row.get("strategyAsOfDate") or row.get("date") or now_tw().split()[0]

    return {
        "key": key,
        "market": market,
        "code": code,
        "name": name,
        "createdAt": created,
        "originalBuyLow": round(min(entry_low, entry_high), 2) if entry_low and entry_high else round(entry_low or entry_high, 2),
        "originalBuyHigh": round(max(entry_low, entry_high), 2) if entry_low and entry_high else round(entry_low or entry_high, 2),
        "originalStopLoss": round(stop, 2),
        "originalTarget": round(target, 2),
        "originalNeckline": round(n(row.get("neckline")), 2),
        "originalStage": row.get("stage") or row.get("status") or "-",
        "originalWinRate": row.get("winRate") or "-",
        "status": "active",
        "createdBy": "strategy-lock-v1",
    }


def process_market(market, payload, locks):
    stocks = payload.get("stocks", []) if isinstance(payload, dict) else []
    candidates = [s for s in stocks if n(s.get("score")) >= 3 and n(s.get("target")) > 0 and n(s.get("stopLoss")) > 0]
    candidates = sorted(candidates, key=rank_score, reverse=True)[:MAX_LOCKS_PER_MARKET]

    for row in candidates:
        key = stock_key(market, row)
        if not key or key.endswith(":None"):
            continue
        if key not in locks:
            locks[key] = create_lock(market, row)
        if locks[key].get("status") != "ended":
            locks[key].update(current_status(row, locks[key]))
            if locks[key].get("trackingStatus") == "策略結束":
                locks[key]["status"] = "ended"
    return locks


def main():
    DATA_DIR.mkdir(exist_ok=True)
    locks = read_json(LOCK_PATH, {})
    tw = read_json(TW_PATH, {"stocks": []})
    us = read_json(US_PATH, {"stocks": []})

    locks = process_market("台股", tw, locks)
    locks = process_market("美股", us, locks)

    output = dict(sorted(locks.items(), key=lambda kv: (kv[1].get("market", ""), kv[1].get("code") or "")))
    LOCK_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"strategy locks updated: {len(output)}")


if __name__ == "__main__":
    main()
