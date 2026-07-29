import json
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
TW_PATH = DATA_DIR / "latest.json"
US_PATH = DATA_DIR / "us_latest.json"
LOCKS_PATH = DATA_DIR / "strategy_locks.json"
TRACKING_PATH = DATA_DIR / "strategy_tracking.json"
TAIPEI_TZ = timezone(timedelta(hours=8))

ACTIVE_STATUSES = {
    "WATCHING",
    "WAIT_CONFIRM",
    "BREAKOUT_CONFIRMED",
    "PULLBACK_CONFIRM",
    "IN_TREND",
    "NEAR_TARGET",
}
ENDED_STATUSES = {"TARGET_HIT", "FAILED", "EXPIRED"}
STATUS_TEXT = {
    "WATCHING": "追蹤中，等待確認",
    "WAIT_CONFIRM": "接近買點，等待收盤確認",
    "BREAKOUT_CONFIRMED": "突破確認，策略成立",
    "PULLBACK_CONFIRM": "回踩不破，可持續追蹤",
    "IN_TREND": "主升段進行中",
    "NEAR_TARGET": "接近目標，注意分批停利",
    "TARGET_HIT": "目標達成，完成追蹤",
    "FAILED": "型態失敗，移出追蹤",
    "EXPIRED": "觀察過期，移出追蹤",
}
TRACKING_SIGNAL_TEXTS = {
    "今日優先觀察",
    "主升候選",
    "突破買點",
    "回踩買點",
    "等待確認",
    "降級觀察",
}


def now_tw():
    return datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")


def n(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(str(value).replace(",", ""))
    except Exception:
        return default


def rounded(value, digits=2):
    return round(n(value), digits)


def is_true(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def pick(source, *names, default=None):
    source = source or {}
    for name in names:
        value = source.get(name)
        if value is not None and value != "":
            return value
    return default


def read_json(path, default):
    try:
        if not path.exists():
            return default
        text = path.read_text(encoding="utf-8").strip()
        return json.loads(text) if text else default
    except Exception as exc:
        print(f"WARN: failed to read {path}: {exc}")
        return default


def download_price_frames(symbols):
    if not symbols:
        return {}
    try:
        import yfinance as yf
    except Exception as exc:
        print(f"WARN: yfinance unavailable for tracking refresh: {exc}")
        return {}

    frames = {}
    for start in range(0, len(symbols), 50):
        batch = symbols[start : start + 50]
        try:
            downloaded = yf.download(
                batch,
                period="3mo",
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                progress=False,
                threads=True,
                timeout=25,
            )
        except Exception as exc:
            print(f"WARN: tracking price download failed: {exc}")
            continue
        if downloaded is None or downloaded.empty:
            continue
        levels = getattr(downloaded.columns, "nlevels", 1)
        for symbol in batch:
            try:
                if levels > 1 and symbol in downloaded.columns.get_level_values(0):
                    frame = downloaded[symbol].copy()
                elif levels > 1 and symbol in downloaded.columns.get_level_values(1):
                    frame = downloaded.xs(symbol, axis=1, level=1).copy()
                elif len(batch) == 1:
                    frame = downloaded.copy()
                else:
                    continue
                frame = frame.dropna(subset=["Close"])
                if not frame.empty:
                    frames[symbol] = frame
            except Exception as exc:
                print(f"WARN: failed to normalize {symbol} tracking data: {exc}")
    return frames


def price_row_from_frame(market, code, name, frame, record):
    close_series = frame["Close"].astype(float).dropna()
    if close_series.empty:
        return None
    latest_index = close_series.index[-1]
    latest = frame.loc[latest_index]
    original = record.get("originalStrategy") or {}
    previous = record.get("latestStatus") or {}
    close = n(latest.get("Close"))
    neckline = n(original.get("originalNeckline"))
    stop = n(original.get("originalStopLoss"))
    target = n(original.get("originalTarget"))
    watch = n(original.get("originalWatchPrice"), close)
    risk_amount = watch - stop if watch and stop else 0
    reward_amount = target - watch if watch and target else 0
    safe_score = n(
        previous.get("latestSafetyScore"),
        n(original.get("originalSafetyScore")),
    )
    return {
        "_trackingOnly": True,
        "strategyAsOfDate": str(latest_index)[:10],
        "date": str(latest_index)[:10],
        "code": code,
        "ticker": code,
        "name": name,
        "score": 4 if safe_score >= 85 else 3,
        "grade": "A" if safe_score >= 85 else "B",
        "safeScore": safe_score,
        "riskRewardRatio": (
            reward_amount / risk_amount
            if risk_amount > 0 and reward_amount > 0
            else n(original.get("originalRiskReward"))
        ),
        "riskPct": risk_amount / watch * 100 if watch and risk_amount > 0 else 0,
        "distanceFromNecklinePct": (
            (close - neckline) / neckline * 100 if neckline else 999
        ),
        "close": close,
        "high": n(latest.get("High"), close),
        "low": n(latest.get("Low"), close),
        "volume": int(n(latest.get("Volume"))),
        "ma5": n(close_series.tail(5).mean()),
        "ma10": n(close_series.tail(10).mean()),
        "ma20": n(close_series.tail(20).mean()),
        "neckline": neckline,
        "observationEntry": watch,
        "chaseRangeLow": n(original.get("originalBuyZoneLow"), watch),
        "chaseRangeHigh": n(original.get("originalBuyZoneHigh"), watch),
        "stopLoss": stop,
        "target": target,
        "stage": original.get("originalStage") or "策略追蹤",
        "originalSignal": original.get("originalSignal") or "等待確認",
        "adjustedSignal": previous.get("adjustedSignal") or "",
        "strictOk": safe_score >= 85,
        "forbiddenChase": False,
        "trackingPriceSource": "Yahoo Finance 完整收盤日K",
        "market": market,
    }


def supplement_active_tracking_rows(existing, market, payload):
    payload = deepcopy(payload) if isinstance(payload, dict) else {"meta": {}, "stocks": []}
    rows = [row for row in payload.get("stocks", []) if isinstance(row, dict)]
    present = {stock_code(market, row) for row in rows}
    active = [
        record
        for record in existing.get("records", [])
        if record.get("market") == market
        and record.get("trackingStatus") in ACTIVE_STATUSES
        and not record.get("manualRemoved")
        and str(record.get("stockCode") or "") not in present
    ]
    if not active:
        payload["stocks"] = rows
        return payload

    code_records = {str(record.get("stockCode")): record for record in active}
    if market == "美股":
        symbol_to_code = {code: code for code in code_records}
        frames = download_price_frames(list(symbol_to_code))
    else:
        symbol_to_code = {f"{code}.TW": code for code in code_records}
        frames = download_price_frames(list(symbol_to_code))
        found_codes = {symbol_to_code[symbol] for symbol in frames}
        otc_symbols = {
            f"{code}.TWO": code for code in code_records if code not in found_codes
        }
        symbol_to_code.update(otc_symbols)
        frames.update(download_price_frames(list(otc_symbols)))

    added = set()
    for symbol, frame in frames.items():
        code = symbol_to_code.get(symbol)
        if not code or code in added:
            continue
        record = code_records[code]
        row = price_row_from_frame(
            market,
            code,
            str(record.get("stockName") or code),
            frame,
            record,
        )
        if row:
            rows.append(row)
            added.add(code)
    if active:
        print(
            f"tracking refresh {market}: requested={len(active)} "
            f"updated={len(added)}"
        )
    payload["stocks"] = rows
    return payload


def strategy_date(row, meta=None):
    value = pick(
        row,
        "strategyAsOfDate",
        "date",
        default=pick(meta or {}, "strategyAsOfDate", "updatedAt", "updatedAtTW", default=""),
    )
    return str(value)[:10] if value else datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")


def stock_code(market, row):
    value = pick(row, "code", "ticker") if market == "台股" else pick(row, "ticker", "code")
    if value is None:
        return ""
    value = str(value).strip()
    return value.upper() if market == "美股" else value


def stock_key(market, row):
    code = stock_code(market, row)
    return f"{market}:{code}" if code else ""


def market_context(payload):
    payload = payload if isinstance(payload, dict) else {}
    meta = payload.get("meta") or {}
    market = payload.get("market") if isinstance(payload.get("market"), dict) else {}
    market_filter = (
        payload.get("marketFilter")
        or meta.get("marketFilter")
        or market.get("marketFilter")
        or market
        or {}
    )
    regime = (
        market_filter.get("marketRegime")
        or market.get("marketRegime")
        or meta.get("marketRegime")
        or "NO_DATA"
    )
    label = (
        market_filter.get("regimeLabel")
        or market.get("regimeLabel")
        or meta.get("marketRegimeLabel")
        or regime
    )
    return str(regime), str(label)


def original_prices(row):
    neckline = n(row.get("neckline"))
    watch = n(pick(row, "observationEntry", "entry", "entryBreakout", default=row.get("close")))
    low = n(pick(row, "chaseRangeLow", "buyLow", "entryPullback", default=watch))
    high = n(pick(row, "chaseRangeHigh", "buyHigh", "entryBreakout", default=watch))
    if low and high and low > high:
        low, high = high, low
    return {
        "neckline": neckline,
        "watch": watch,
        "buyLow": low,
        "buyHigh": high,
        "stop": n(row.get("stopLoss")),
        "target": n(row.get("target")),
    }


def tracking_metrics(row):
    prices = original_prices(row)
    entry = prices["watch"]
    risk_amount = entry - prices["stop"] if entry and prices["stop"] else 0
    reward_amount = prices["target"] - entry if entry and prices["target"] else 0
    risk_pct = n(
        row.get("riskPct"),
        risk_amount / entry * 100 if entry and risk_amount > 0 else 0,
    )
    risk_reward = n(
        row.get("riskRewardRatio"),
        reward_amount / risk_amount if risk_amount > 0 and reward_amount > 0 else 0,
    )
    close = n(row.get("close"))
    distance = n(
        row.get("distanceFromNecklinePct"),
        (close - prices["neckline"]) / prices["neckline"] * 100
        if close and prices["neckline"]
        else 999,
    )
    safe_score = n(row.get("safeScore"), -1)
    if safe_score < 0:
        safe_score = 0
        absolute_distance = abs(distance)
        if absolute_distance <= 2:
            safe_score += 30
        elif absolute_distance <= 3.5:
            safe_score += 24
        elif absolute_distance <= 5:
            safe_score += 14
        if 0 < risk_pct <= 3.5:
            safe_score += 25
        elif risk_pct <= 5:
            safe_score += 20
        elif risk_pct <= 6.5:
            safe_score += 12
        if risk_reward >= 2.2:
            safe_score += 25
        elif risk_reward >= 2:
            safe_score += 22
        elif risk_reward >= 1.5:
            safe_score += 14
        score = n(row.get("score"))
        safe_score += 10 if score >= 4 else 7 if score >= 3 else 0
        volume = n(row.get("volume"))
        average = n(row.get("avgVolume20"))
        if average and volume / average >= 1.5:
            safe_score += 5
        safe_score = max(0, min(100, round(safe_score)))
    return {
        "riskPct": risk_pct,
        "riskRewardRatio": risk_reward,
        "distanceFromNecklinePct": distance,
        "safeScore": safe_score,
    }


def tracking_trigger_reasons(row):
    prices = original_prices(row)
    if not all([prices["neckline"], prices["stop"], prices["target"]]):
        return []
    if n(row.get("score")) < 3 and str(row.get("grade", "")).upper() not in {"A", "B"}:
        return []

    adjusted = str(pick(row, "adjustedSignal", "originalSignal", "action", "status", default=""))
    grade = str(row.get("grade", "")).upper()
    metrics = tracking_metrics(row)
    safe_score = metrics["safeScore"]
    risk_reward = metrics["riskRewardRatio"]
    distance = metrics["distanceFromNecklinePct"]
    risk_pct = metrics["riskPct"]
    reasons = []

    if is_true(row.get("mainPick")) or is_true(row.get("strictOk")) or grade == "A":
        reasons.append("進入今日優先觀察")
    if grade in {"A", "B"} or (n(row.get("score")) >= 3 and not is_true(row.get("forbiddenChase"))):
        reasons.append("產生主升段安全觀察策略卡")
    if any(text in adjusted for text in TRACKING_SIGNAL_TEXTS):
        reasons.append(f"調整後訊號：{adjusted}")
    if safe_score >= 85:
        reasons.append("安全分達 85 以上")
    if risk_reward >= 2:
        reasons.append("風報比達 2 以上")
    if 0 <= distance <= 5:
        reasons.append("距離頸線在 0%～5%")
    if 0 < risk_pct < 5.5:
        reasons.append("停損距離小於 5.5%")
    return list(dict.fromkeys(reasons))


def default_market_actions(row, regime):
    original_signal = str(
        pick(row, "originalSignal", "buyPointType", "stage", "planStatus", "status", default="追蹤中")
    )
    adjusted = str(pick(row, "adjustedSignal", default=original_signal))
    holder = str(pick(row, "holderAction", default="持有者依原始停損與原始目標管理。"))
    cash = str(pick(row, "cashAction", "emptyAction", default="空手者等待收盤確認，不追價。"))
    warning = str(pick(row, "riskWarning", "riskText", default="原始策略不可被新策略卡覆蓋。"))

    if regime == "WATCH" and "突破" in original_signal:
        adjusted = "觀察，不追價"
        cash = "突破買點降級，只等收盤確認或回踩不破。"
        warning = "大盤未創高，不追價，只等回踩不破。"
    elif regime == "DEFENSE":
        adjusted = "取消進場，降級觀察" if "突破" in original_signal else "降級觀察"
        holder = "提高停利 / 停損警戒，仍以原始策略管理。"
        cash = "不追價，等待大盤止跌。"
        warning = "大盤轉弱，主升段股容易補跌。"
    elif regime == "STOP":
        adjusted = "取消新進場，只保留觀察"
        holder = "只看續抱、停利或停損，不新增部位。"
        cash = "停止新進場，先保留現金。"
        warning = "停止新進場，禁止把追蹤紀錄視為進場訊號。"
    elif regime == "NO_DATA":
        adjusted = "資料不足，暫停新進場"
        cash = "等待資料成功更新後再判斷。"
        warning = "大盤資料不足，追蹤紀錄只供原始策略管理。"

    return original_signal, adjusted, holder, cash, warning


def build_latest_status(row, regime):
    original_signal, adjusted, holder, cash, warning = default_market_actions(row, regime)
    close = n(row.get("close"))
    metrics = tracking_metrics(row)
    return {
        "latestClose": rounded(close),
        "latestHigh": rounded(pick(row, "high", default=close)),
        "latestLow": rounded(pick(row, "low", default=close)),
        "latestVolume": int(n(row.get("volume"))),
        "latestMA5": rounded(row.get("ma5")),
        "latestMA10": rounded(row.get("ma10")),
        "latestMA20": rounded(row.get("ma20")),
        "latestSafetyScore": int(round(metrics["safeScore"])),
        "latestRiskReward": rounded(metrics["riskRewardRatio"]),
        "latestMarketRegime": regime,
        "latestSignal": original_signal,
        "adjustedSignal": adjusted,
        "holderAction": holder,
        "cashAction": cash,
        "riskWarning": warning,
    }


def append_system_note(record, date, message):
    notes = record.setdefault("notes", {"systemNotes": [], "manualNote": ""})
    system_notes = notes.setdefault("systemNotes", [])
    entry = {"date": date, "text": message}
    if entry not in system_notes:
        system_notes.append(entry)
        notes["systemNotes"] = system_notes[-60:]
    notes.setdefault("manualNote", "")


def status_result(status):
    return {
        "TARGET_HIT": "目標達成",
        "FAILED": "型態失敗",
        "EXPIRED": "觀察過期",
    }.get(status, "")


def update_tracking_status(tracking_record, latest_market_data, market_regime, current_date=None):
    record = deepcopy(tracking_record)
    row = latest_market_data or {}
    date = current_date or strategy_date(row)
    original = record.get("originalStrategy") or {}
    neckline = n(original.get("originalNeckline"))
    buy_low = n(original.get("originalBuyZoneLow"))
    buy_high = n(original.get("originalBuyZoneHigh"))
    stop = n(original.get("originalStopLoss"))
    target = n(original.get("originalTarget"))
    latest = build_latest_status(row, market_regime)
    close = n(latest.get("latestClose"))
    high = n(latest.get("latestHigh"), close)
    low = n(latest.get("latestLow"), close)
    safe_score = n(latest.get("latestSafetyScore"))

    dates = list(dict.fromkeys([str(x)[:10] for x in record.get("trackingDates", []) if x]))
    if date not in dates:
        dates.append(date)
    record["trackingDates"] = sorted(dates)
    tracking_days = len(record["trackingDates"])

    previous_progress = record.get("progress") or {}
    previously_broke = bool(previous_progress.get("everBrokeNeckline"))
    broke_neckline = bool(neckline and high >= neckline)
    close_held = bool(neckline and close >= neckline)
    ever_broke = previously_broke or broke_neckline
    in_buy_zone = bool(
        buy_low
        and buy_high
        and high >= buy_low
        and low <= buy_high
    )
    above_buy_zone = bool(buy_high and close > buy_high)
    pullback_held = bool(
        previously_broke
        and neckline
        and low <= neckline * 1.02
        and close >= neckline * 0.995
    )
    stop_broken = bool(stop and close <= stop)
    near_target = bool(target and close >= target * 0.9)
    target_hit = bool(target and max(high, close) >= target)
    lost_neckline_after_breakout = bool(
        previously_broke and neckline and close < neckline * 0.97
    )
    pattern_failed = stop_broken or lost_neckline_after_breakout or is_true(row.get("patternInvalid"))
    breakout_confirmed = bool(
        neckline
        and close >= neckline * 1.02
        and n(row.get("score")) >= 3
        and not pattern_failed
    )
    expired = bool(
        tracking_days > 15
        and not ever_broke
        and (not neckline or close < neckline or safe_score < 70)
    )

    if target_hit:
        status = "TARGET_HIT"
    elif pattern_failed:
        status = "FAILED"
    elif near_target:
        status = "NEAR_TARGET"
    elif pullback_held:
        status = "PULLBACK_CONFIRM"
    elif previously_broke and close_held and not in_buy_zone:
        status = "IN_TREND"
    elif breakout_confirmed:
        status = "BREAKOUT_CONFIRMED"
    elif expired:
        status = "EXPIRED"
    elif in_buy_zone or (neckline and close >= neckline * 0.98):
        status = "WAIT_CONFIRM"
    else:
        status = "WATCHING"

    if status == "FAILED":
        latest["holderAction"] = "跌破原始停損或型態破壞，優先處理。"
        latest["cashAction"] = "取消觀察，等待新的主升段結構。"
        latest["riskWarning"] = "原始策略已失敗，不可用新策略卡把停損往下移。"
    elif status == "TARGET_HIT":
        latest["holderAction"] = "已達原始第一目標，優先執行分批停利。"
        latest["cashAction"] = "目標已達成，空手者不追高。"
    elif status == "NEAR_TARGET":
        latest["holderAction"] = "接近原始第一目標，準備分批停利。"
        latest["cashAction"] = "接近目標，空手者不追高。"
    elif status == "EXPIRED":
        latest["holderAction"] = "原觀察策略已過期，重新檢查持股風險。"
        latest["cashAction"] = "移出進行中追蹤，等待新的完整結構。"

    record["latestStatus"] = latest
    record["trackingStatus"] = status
    record["trackingStatusText"] = STATUS_TEXT[status]
    record["lastUpdateDate"] = date
    record["progress"] = {
        "brokeNeckline": broke_neckline,
        "closeHeldNeckline": close_held,
        "inBuyZone": in_buy_zone,
        "aboveBuyZoneHigh": above_buy_zone,
        "pullbackHeld": pullback_held,
        "stopBroken": stop_broken,
        "nearTarget": near_target,
        "targetHit": target_hit,
        "patternFailed": pattern_failed,
        "expired": expired,
        "everBrokeNeckline": ever_broke,
        "trackingDays": tracking_days,
    }

    history = [x for x in record.get("statusHistory", []) if str(x.get("date")) != date]
    history.append({"date": date, "status": status, "close": rounded(close)})
    record["statusHistory"] = sorted(history, key=lambda x: str(x.get("date", "")))[-60:]

    if status in ENDED_STATUSES:
        record["endDate"] = date
        record["endReason"] = STATUS_TEXT[status]
        record["result"] = status_result(status)
    else:
        record["endDate"] = None
        record["endReason"] = ""
        record["result"] = ""
    return record


def create_tracking_record(market, row, meta=None, trigger_reasons=None):
    date = strategy_date(row, meta)
    code = stock_code(market, row)
    name = str(pick(row, "name", default=code))
    regime, label = market_context({"meta": meta or {}})
    prices = original_prices(row)
    metrics = tracking_metrics(row)
    original_signal = str(
        pick(row, "originalSignal", "buyPointType", "stage", "planStatus", "status", default="追蹤中")
    )
    impact = str(
        pick(
            row,
            "marketRegimeImpact",
            default=f"{regime} {label}" if regime != "NO_DATA" else "資料不足",
        )
    )
    record = {
        "trackingId": f"{market}:{code}:{date}",
        "market": market,
        "stockCode": code,
        "stockName": name,
        "firstSignalDate": date,
        "lastUpdateDate": date,
        "endDate": None,
        "endReason": "",
        "result": "",
        "trackingStatus": "WATCHING",
        "trackingStatusText": STATUS_TEXT["WATCHING"],
        "originalStrategy": {
            "originalStage": str(pick(row, "stage", "category", "status", default="-")),
            "originalSignal": original_signal,
            "originalNeckline": rounded(prices["neckline"]),
            "originalWatchPrice": rounded(prices["watch"]),
            "originalBuyZoneLow": rounded(prices["buyLow"]),
            "originalBuyZoneHigh": rounded(prices["buyHigh"]),
            "originalStopLoss": rounded(prices["stop"]),
            "originalTarget": rounded(prices["target"]),
            "originalRiskReward": rounded(metrics["riskRewardRatio"]),
            "originalSafetyScore": int(round(metrics["safeScore"])),
            "originalMarketRegime": regime,
            "originalMarketRegimeImpact": impact,
        },
        "latestStatus": {},
        "progress": {},
        "notes": {
            "systemNotes": [
                {
                    "date": date,
                    "text": "；".join(trigger_reasons or ["建立策略追蹤紀錄"]),
                }
            ],
            "manualNote": "",
        },
        "trackingDates": [],
        "statusHistory": [],
        "manualRemoved": False,
        "manualRemovedAt": None,
    }
    return update_tracking_status(record, row, regime, date)


def create_record_from_lock(market, lock, row, meta=None):
    record = create_tracking_record(
        market,
        row,
        meta,
        ["由既有策略鎖定紀錄匯入；缺少的原始欄位以首次匯入值保存"],
    )
    original = record["originalStrategy"]
    original["originalStage"] = str(pick(lock, "originalStage", default=original["originalStage"]))
    original["originalNeckline"] = rounded(
        pick(lock, "originalNeckline", default=original["originalNeckline"])
    )
    original["originalBuyZoneLow"] = rounded(
        pick(lock, "originalBuyLow", default=original["originalBuyZoneLow"])
    )
    original["originalBuyZoneHigh"] = rounded(
        pick(lock, "originalBuyHigh", default=original["originalBuyZoneHigh"])
    )
    original["originalStopLoss"] = rounded(
        pick(lock, "originalStopLoss", default=original["originalStopLoss"])
    )
    original["originalTarget"] = rounded(
        pick(lock, "originalTarget", default=original["originalTarget"])
    )
    created = str(pick(lock, "createdAt", default=record["firstSignalDate"]))[:10]
    record["firstSignalDate"] = created
    record["trackingId"] = f"{market}:{record['stockCode']}:{created}"
    return update_tracking_status(record, row, record["latestStatus"]["latestMarketRegime"])


def find_active_record(records, market, code):
    for record in records:
        if (
            record.get("market") == market
            and str(record.get("stockCode")) == str(code)
            and record.get("trackingStatus") in ACTIVE_STATUSES
            and not record.get("manualRemoved")
        ):
            return record
    return None


def newest_ended_record(records, market, code):
    ended = [
        record
        for record in records
        if record.get("market") == market
        and str(record.get("stockCode")) == str(code)
        and record.get("trackingStatus") in ENDED_STATUSES
    ]
    return max(ended, key=lambda x: str(x.get("endDate") or ""), default=None)


def process_market(records, market, payload, locks=None):
    payload = payload if isinstance(payload, dict) else {}
    meta = payload.get("meta") or {}
    rows = [row for row in payload.get("stocks", []) if isinstance(row, dict)]
    regime, _ = market_context(payload)
    rows_by_code = {stock_code(market, row): row for row in rows if stock_code(market, row)}
    date = str(pick(meta, "strategyAsOfDate", default=""))[:10]

    for index, record in enumerate(records):
        if record.get("market") != market or record.get("trackingStatus") not in ACTIVE_STATUSES:
            continue
        row = rows_by_code.get(str(record.get("stockCode")))
        if row:
            records[index] = update_tracking_status(
                record,
                row,
                regime,
                strategy_date(row, meta),
            )
        elif date:
            append_system_note(record, date, "本日未取得個股完整資料，保留原追蹤紀錄且不改變原始策略。")

    for lock in (locks or {}).values():
        if not isinstance(lock, dict) or lock.get("market") != market or lock.get("status") == "ended":
            continue
        code = str(pick(lock, "code", default=""))
        row = rows_by_code.get(code)
        if not row or find_active_record(records, market, code):
            continue
        if newest_ended_record(records, market, code):
            continue
        records.append(create_record_from_lock(market, lock, row, meta))

    for row in rows:
        if row.get("_trackingOnly"):
            continue
        reasons = tracking_trigger_reasons(row)
        if not reasons:
            continue
        code = stock_code(market, row)
        current_date = strategy_date(row, meta)
        active = find_active_record(records, market, code)
        if active:
            append_system_note(active, current_date, "今日再次出現新觀察訊號，原始策略維持不變。")
            continue
        ended = newest_ended_record(records, market, code)
        if ended and str(ended.get("endDate")) >= current_date:
            continue
        records.append(create_tracking_record(market, row, meta, reasons))
    return records


def process_tracking(existing, tw_payload, us_payload, locks=None):
    existing = existing if isinstance(existing, dict) else {}
    records = [
        deepcopy(record)
        for record in existing.get("records", [])
        if isinstance(record, dict) and record.get("trackingId")
    ]
    records = process_market(records, "台股", tw_payload, locks)
    records = process_market(records, "美股", us_payload, locks)
    records.sort(
        key=lambda record: (
            record.get("trackingStatus") in ENDED_STATUSES,
            str(record.get("lastUpdateDate") or ""),
            str(record.get("market") or ""),
            str(record.get("stockCode") or ""),
        ),
        reverse=False,
    )
    active_count = sum(record.get("trackingStatus") in ACTIVE_STATUSES for record in records)
    ended_count = sum(record.get("trackingStatus") in ENDED_STATUSES for record in records)
    return {
        "meta": {
            "updatedAt": now_tw(),
            "version": "strategy-tracking-v1",
            "activeCount": active_count,
            "endedCount": ended_count,
            "maxWaitingTradingDays": 15,
        },
        "records": records,
    }


def main():
    DATA_DIR.mkdir(exist_ok=True)
    existing = read_json(TRACKING_PATH, {"meta": {}, "records": []})
    tw_payload = read_json(TW_PATH, {"meta": {}, "stocks": []})
    us_payload = read_json(US_PATH, {"meta": {}, "stocks": []})
    locks = read_json(LOCKS_PATH, {})
    if not isinstance(locks, dict):
        locks = {}
    tw_payload = supplement_active_tracking_rows(existing, "台股", tw_payload)
    us_payload = supplement_active_tracking_rows(existing, "美股", us_payload)
    output = process_tracking(existing, tw_payload, us_payload, locks)
    TRACKING_PATH.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        "strategy tracking updated: "
        f"active={output['meta']['activeCount']} ended={output['meta']['endedCount']}"
    )


if __name__ == "__main__":
    main()

