import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
TW_LATEST = DATA_DIR / "latest.json"
US_LATEST = DATA_DIR / "us_latest.json"
SNAPSHOTS = DATA_DIR / "strategy_snapshots.json"
MAX_DAYS = 5


def load_json(path, default):
    try:
        if not path.exists():
            return default
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return default
        return json.loads(text)
    except Exception as exc:
        print(f"WARN: failed to read {path}: {exc}")
        return default


def sf(value, default=0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def sr(value, digits=2):
    value = sf(value)
    return round(value, digits)


def pick(row, *names, default=None):
    for name in names:
        value = row.get(name)
        if value is not None and value != "":
            return value
    return default


def snapshot_key(market, row):
    code = row.get("code") if market == "台股" else pick(row, "ticker", "code")
    if not code:
        return None
    return f"{market}:{code}"


def normalize_row(row, market, meta):
    key = snapshot_key(market, row)
    if not key:
        return None

    code = row.get("code") if market == "台股" else pick(row, "ticker", "code")
    date = pick(row, "strategyAsOfDate", "date", default=meta.get("strategyAsOfDate"))
    if not date:
        return None

    close = sf(row.get("close"))
    entry = sf(pick(row, "observationEntry", "entry", "entryBreakout", default=close))
    neckline = sf(row.get("neckline"))
    buy_low = sf(pick(row, "chaseRangeLow", "buyLow", "entryPullback", default=entry))
    buy_high = sf(pick(row, "chaseRangeHigh", "buyHigh", "entryBreakout", default=entry))
    stop_loss = sf(row.get("stopLoss"))
    target = sf(row.get("target"))

    return key, {
        "date": str(date),
        "market": market,
        "code": str(code),
        "name": str(pick(row, "name", default=code)),
        "grade": str(pick(row, "grade", default="")),
        "safeScore": int(sf(row.get("safeScore"))),
        "riskRewardRatio": sr(row.get("riskRewardRatio")),
        "category": str(pick(row, "category", "status", default="")),
        "close": sr(close),
        "neckline": sr(neckline),
        "entry": sr(entry),
        "buyLow": sr(buy_low),
        "buyHigh": sr(buy_high),
        "stopLoss": sr(stop_loss),
        "target": sr(target),
        "riskPct": sr(row.get("riskPct")),
        "rewardPct": sr(row.get("rewardPct")),
        "distanceFromNecklinePct": sr(row.get("distanceFromNecklinePct")),
        "pattern": str(pick(row, "pattern", default="")),
        "reason": str(pick(row, "reason", default="")),
        "action": str(pick(row, "action", "planStatus", "status", default="")),
    }


def merge_snapshot(existing_rows, new_row):
    rows = [r for r in existing_rows if isinstance(r, dict)]
    rows = [r for r in rows if str(r.get("date")) != str(new_row.get("date"))]
    rows.append(new_row)
    rows.sort(key=lambda r: str(r.get("date", "")), reverse=True)
    return rows[:MAX_DAYS]


def collect_from_payload(payload, market):
    meta = payload.get("meta") or {}
    rows = payload.get("stocks") or []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            snap = normalize_row(row, market, meta)
            if snap:
                out.append(snap)
        except Exception as exc:
            print(f"WARN: skip {market} row: {exc}")
    return out


def main():
    DATA_DIR.mkdir(exist_ok=True)
    snapshots = load_json(SNAPSHOTS, {})
    if not isinstance(snapshots, dict):
        snapshots = {}

    sources = [
        (TW_LATEST, "台股"),
        (US_LATEST, "美股"),
    ]

    updated = 0
    for path, market in sources:
        payload = load_json(path, {"meta": {}, "stocks": []})
        for key, row in collect_from_payload(payload, market):
            snapshots[key] = merge_snapshot(snapshots.get(key, []), row)
            updated += 1

    SNAPSHOTS.write_text(json.dumps(snapshots, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Strategy snapshots updated: {updated} rows")


if __name__ == "__main__":
    main()
