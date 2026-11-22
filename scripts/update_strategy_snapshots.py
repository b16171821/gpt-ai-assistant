import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
TW_LATEST = DATA_DIR / "latest.json"
US_LATEST = DATA_DIR / "us_latest.json"
TW_CSV = DATA_DIR / "latest.csv"
US_CSV = DATA_DIR / "us_latest.csv"
SNAPSHOTS = DATA_DIR / "strategy_snapshots.json"
MAX_DAYS = 5


def load_json(path, default):
    try:
        if not path.exists():
            return default
        text = path.read_text(encoding="utf-8-sig").strip()
        if not text:
            return default
        return json.loads(text)
    except Exception as exc:
        print(f"WARN: failed to read {path}: {exc}")
        return default


def load_csv(path):
    try:
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))
    except Exception as exc:
        print(f"WARN: failed to read {path}: {exc}")
        return []


def sf(value, default=0):
    try:
        if value is None or value == "":
            return default
        return float(str(value).replace(",", ""))
    except Exception:
        return default


def sr(value, digits=2):
    return round(sf(value), digits)


def pick(row, *names, default=None):
    for name in names:
        value = row.get(name)
        if value is not None and value != "":
            return value
    return default


def truthy(value):
    return str(value).strip().lower() in ("1", "true", "yes", "y")


def snapshot_key(market, row):
    code = row.get("code") if market == "台股" else pick(row, "ticker", "code")
    if not code:
        return None
    return f"{market}:{code}"


def risk_fields(row):
    close = sf(row.get("close"))
    neckline = sf(row.get("neckline"))
    entry = sf(pick(row, "observationEntry", "entry", "entryBreakout", default=close or neckline))
    buy_low = sf(pick(row, "chaseRangeLow", "buyLow", "entryPullback", default=entry))
    buy_high = sf(pick(row, "chaseRangeHigh", "buyHigh", "entryBreakout", default=entry))
    stop_loss = sf(row.get("stopLoss"))
    target = sf(row.get("target"))
    risk_amt = entry - stop_loss if entry and stop_loss else 0
    reward_amt = target - entry if entry and target else 0
    risk_pct = sf(row.get("riskPct"), risk_amt / entry * 100 if entry and risk_amt else 0)
    reward_pct = sf(row.get("rewardPct"), reward_amt / entry * 100 if entry and reward_amt else 0)
    rr = sf(row.get("riskRewardRatio"), reward_amt / risk_amt if risk_amt > 0 and reward_amt > 0 else 0)
    dist = sf(row.get("distanceFromNecklinePct"), (close - neckline) / neckline * 100 if close and neckline else 999)
    return {
        "close": close,
        "neckline": neckline,
        "entry": entry,
        "buyLow": buy_low,
        "buyHigh": buy_high,
        "stopLoss": stop_loss,
        "target": target,
        "riskPct": risk_pct,
        "rewardPct": reward_pct,
        "riskRewardRatio": rr,
        "distanceFromNecklinePct": dist,
        "valid": bool(neckline and entry and stop_loss and target and rr > 0 and risk_pct > 0),
    }


def derive_summary(row, market, risk):
    if row.get("strategySystem") == "apo-five-lights-1.0" or row.get("rawRating") or row.get("rating"):
        rating = row.get("rating") or row.get("rawRating") or "未核實"
        return rating, int(sf(row.get("safeScore"))), row.get("category") or rating, row.get("action") or ""
    return "未核實", 0, "未核實", "舊資料未以五燈重算，不沿用絕對值距離重新評級"


def normalize_row(row, market, meta):
    if sf(row.get("score")) < 3:
        return None
    key = snapshot_key(market, row)
    if not key:
        return None

    code = row.get("code") if market == "台股" else pick(row, "ticker", "code")
    date = pick(row, "strategyAsOfDate", "date", default=meta.get("strategyAsOfDate") or meta.get("updatedAtTW"))
    if not date:
        return None

    risk = risk_fields(row)
    grade, safe_score, category, action = derive_summary(row, market, risk)
    return key, {
        "date": str(date)[:10],
        "market": market,
        "code": str(code),
        "name": str(pick(row, "name", default=code)),
        "grade": grade,
        "safeScore": safe_score,
        "riskRewardRatio": sr(risk["riskRewardRatio"]),
        "category": category,
        "close": sr(risk["close"]),
        "neckline": sr(risk["neckline"]),
        "entry": sr(risk["entry"]),
        "buyLow": sr(risk["buyLow"]),
        "buyHigh": sr(risk["buyHigh"]),
        "stopLoss": sr(risk["stopLoss"]),
        "target": sr(risk["target"]),
        "riskPct": sr(risk["riskPct"]),
        "rewardPct": sr(risk["rewardPct"]),
        "distanceFromNecklinePct": sr(risk["distanceFromNecklinePct"]),
        "pattern": str(pick(row, "pattern", default="")),
        "reason": str(pick(row, "reason", default="")),
        "action": str(action),
    }


def merge_snapshot(existing_rows, new_row):
    rows = [r for r in existing_rows if isinstance(r, dict)]
    rows = [r for r in rows if str(r.get("date")) != str(new_row.get("date"))]
    rows.append(new_row)
    rows.sort(key=lambda r: str(r.get("date", "")), reverse=True)
    return rows[:MAX_DAYS]


def collect(path_json, path_csv, market):
    payload = load_json(path_json, None)
    if isinstance(payload, dict) and isinstance(payload.get("stocks"), list) and payload.get("stocks"):
        meta = payload.get("meta") or {}
        rows = payload.get("stocks") or []
    else:
        meta = {}
        rows = load_csv(path_csv)

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

    updated = 0
    for key, row in collect(TW_LATEST, TW_CSV, "台股") + collect(US_LATEST, US_CSV, "美股"):
        snapshots[key] = merge_snapshot(snapshots.get(key, []), row)
        updated += 1

    SNAPSHOTS.write_text(json.dumps(snapshots, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Strategy snapshots updated: {updated} rows")


if __name__ == "__main__":
    main()
