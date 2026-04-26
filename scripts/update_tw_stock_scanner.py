import csv
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
LATEST_JSON = DATA_DIR / "latest.json"
LATEST_CSV = DATA_DIR / "latest.csv"
HISTORY_CSV = DATA_DIR / "history.csv"
TAIPEI_TZ = timezone(timedelta(hours=8))


def now_tw():
    return datetime.now(TAIPEI_TZ).isoformat()


def main():
    DATA_DIR.mkdir(exist_ok=True)

    rows = [
        {
            "date": now_tw()[:10],
            "market": "TWSE",
            "code": "2330",
            "name": "台積電",
            "close": 1000,
            "ma5": 980,
            "ma20": 950,
            "ma60": 900,
            "ma240": 780,
            "rise60": 35,
            "volume": 80000,
            "avgVolume20": 30000,
            "adx": 32,
            "kline": "紅K突破",
            "theme": "AI 半導體 先進封裝",
            "trend": True,
            "fund": True,
            "themeHit": True,
            "priceVol": True,
            "score": 4,
            "status": "測試成功：流程已接通，下一步改接全台股資料",
            "reason": "趨勢多頭、量能放大、題材命中、價量條件足"
        },
        {
            "date": now_tw()[:10],
            "market": "TWSE",
            "code": "4977",
            "name": "眾達-KY",
            "close": 98,
            "ma5": 95,
            "ma20": 91,
            "ma60": 83,
            "ma240": 70,
            "rise60": 42,
            "volume": 12000,
            "avgVolume20": 5000,
            "adx": 28,
            "kline": "紅K突破",
            "theme": "CPO AI 伺服器",
            "trend": True,
            "fund": True,
            "themeHit": True,
            "priceVol": True,
            "score": 4,
            "status": "測試成功：流程已接通，下一步改接全台股資料",
            "reason": "趨勢多頭、量能放大、題材命中、價量條件足"
        }
    ]

    payload = {
        "meta": {
            "updatedAt": now_tw(),
            "mode": "smoke-test",
            "note": "GitHub Actions pipeline is working. Replace with real TWSE/TPEx scanner after this run passes.",
            "total": len(rows),
            "qualified3Plus": len(rows),
            "qualified4": len(rows)
        },
        "stocks": rows
    }

    LATEST_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    with LATEST_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    with HISTORY_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print("Smoke test data generated successfully.")


if __name__ == "__main__":
    main()
