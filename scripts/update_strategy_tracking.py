import json
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
TW_PATH = DATA_DIR / "latest.json"
US_PATH = DATA_DIR / "us_latest.json"
LOCKS_PATH = DATA_DIR / "strategy_locks.json"
SNAPSHOTS_PATH = DATA_DIR / "strategy_snapshots.json"
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
ENDED_STATUSES = {
    "TARGET_HIT",
    "FAILED",
    "EXPIRED",
    "MANUAL_CLOSED",
    "NEED_REVIEW",
}
TERMINAL_STATUSES = set(ENDED_STATUSES)
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
    "MANUAL_CLOSED": "手動結束追蹤",
    "NEED_REVIEW": "同日觸發，需人工確認",
}
TRACKING_SIGNAL_TEXTS = {
    "今日優先觀察",
    "主升候選",
    "突破買點",
    "回踩買點",
    "等待確認",
    "降級觀察",
}
NEW_SIGNAL_NOTICE = (
    "今日重新出現新觀察訊號，但既有持股仍以原始策略為準。"
    "新策略僅供空手者參考。"
)


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


def download_price_frames(symbols, period="3mo"):
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
                period=period,
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


def frame_to_historical_prices(frame):
    prices = []
    if frame is None or frame.empty:
        return prices
    for index, row in frame.iterrows():
        close = n(row.get("Close"))
        if not close:
            continue
        prices.append(
            {
                "date": str(index)[:10],
                "high": n(row.get("High"), close),
                "low": n(row.get("Low"), close),
                "close": close,
            }
        )
    return prices


def download_tracking_histories(existing):
    records = [
        record
        for record in (existing or {}).get("records", [])
        if isinstance(record, dict)
        and record.get("stockCode")
        and record.get("market") in {"台股", "美股"}
    ]
    histories = {}
    for market in ("台股", "美股"):
        market_records = {
            str(record.get("stockCode")): record
            for record in records
            if record.get("market") == market
        }
        if not market_records:
            continue
        if market == "美股":
            symbol_to_code = {code: code for code in market_records}
            frames = download_price_frames(list(symbol_to_code), period="1y")
        else:
            symbol_to_code = {f"{code}.TW": code for code in market_records}
            frames = download_price_frames(list(symbol_to_code), period="1y")
            found_codes = {symbol_to_code[symbol] for symbol in frames}
            otc_symbols = {
                f"{code}.TWO": code
                for code in market_records
                if code not in found_codes
            }
            symbol_to_code.update(otc_symbols)
            frames.update(
                download_price_frames(list(otc_symbols), period="1y")
            )
        for symbol, frame in frames.items():
            code = symbol_to_code.get(symbol)
            if not code:
                continue
            key = f"{market}:{code}"
            prices = frame_to_historical_prices(frame)
            if prices and (
                key not in histories
                or len(prices) > len(histories[key])
            ):
                histories[key] = prices
    print(
        f"tracking lifecycle histories: records={len(records)} "
        f"downloaded={len(histories)}"
    )
    return histories


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
        "MANUAL_CLOSED": "手動結束",
        "NEED_REVIEW": "需人工確認",
    }.get(status, "")


def normalize_historical_prices(historical_prices):
    if historical_prices is None:
        return []
    if isinstance(historical_prices, list):
        source = historical_prices
    elif hasattr(historical_prices, "iterrows"):
        source = []
        for index, row in historical_prices.iterrows():
            source.append(
                {
                    "date": str(index)[:10],
                    "high": row.get("High"),
                    "low": row.get("Low"),
                    "close": row.get("Close"),
                }
            )
    else:
        return []

    rows = []
    for price in source:
        if not isinstance(price, dict):
            continue
        date = str(pick(price, "date", "strategyAsOfDate", default=""))[:10]
        close = n(pick(price, "close", "Close"))
        high = n(pick(price, "high", "High"), close)
        low = n(pick(price, "low", "Low"), close)
        if not date or not close:
            continue
        rows.append(
            {
                "date": date,
                "high": high,
                "low": low,
                "close": close,
            }
        )
    return sorted(rows, key=lambda price: price["date"])


def tracking_lifecycle_prices(record, downloaded_prices):
    prices = list(downloaded_prices or [])
    latest = record.get("latestStatus") or {}
    latest_date = str(
        record.get("lastUpdateDate")
        or record.get("endedAt")
        or record.get("endDate")
        or ""
    )[:10]
    latest_close = n(latest.get("latestClose"))
    if latest_date and latest_close:
        prices.append(
            {
                "date": latest_date,
                "high": n(latest.get("latestHigh"), latest_close),
                "low": n(latest.get("latestLow"), latest_close),
                "close": latest_close,
            }
        )
    for item in record.get("statusHistory", []):
        if not isinstance(item, dict):
            continue
        date = str(item.get("date") or "")[:10]
        close = n(item.get("close"))
        if date and close:
            prices.append(
                {
                    "date": date,
                    "high": close,
                    "low": close,
                    "close": close,
                }
            )
    return prices


def evaluateTrackingLifecycle(tracking_record, historical_prices):
    record = deepcopy(tracking_record)
    original = record.get("originalStrategy") or {}
    target = n(original.get("originalTarget"))
    stop = n(original.get("originalStopLoss"))
    buy_low = n(original.get("originalBuyZoneLow"))
    buy_high = n(original.get("originalBuyZoneHigh"))
    entry = (
        (buy_low + buy_high) / 2
        if buy_low and buy_high
        else n(original.get("originalWatchPrice"), buy_low or buy_high)
    )
    first_date = str(record.get("firstSignalDate") or "")[:10]
    current_status = str(record.get("trackingStatus") or "")
    terminal_cutoff = (
        str(record.get("endedAt") or record.get("endDate") or "")[:10]
        if current_status in TERMINAL_STATUSES
        else ""
    )

    target_hit_date = str(record.get("targetHitDate") or "")[:10] or None
    stop_loss_hit_date = str(record.get("stopLossHitDate") or "")[:10] or None
    max_high = n(record.get("maxHighDuringTracking"))
    prices = normalize_historical_prices(historical_prices)

    for price in prices:
        date = price["date"]
        if first_date and date < first_date:
            continue
        if terminal_cutoff and date > terminal_cutoff:
            continue
        max_high = max(max_high, n(price["high"]), n(price["close"]))
        if (
            target
            and max(n(price["high"]), n(price["close"])) >= target
            and (target_hit_date is None or date < target_hit_date)
        ):
            target_hit_date = date
        if (
            stop
            and n(price["close"]) <= stop
            and (stop_loss_hit_date is None or date < stop_loss_hit_date)
        ):
            stop_loss_hit_date = date

    final_status = None
    if target_hit_date and stop_loss_hit_date:
        if target_hit_date < stop_loss_hit_date:
            final_status = "TARGET_HIT"
        elif stop_loss_hit_date < target_hit_date:
            final_status = "FAILED"
        else:
            final_status = "NEED_REVIEW"
    elif target_hit_date:
        final_status = "TARGET_HIT"
    elif stop_loss_hit_date:
        final_status = "FAILED"
    elif current_status in TERMINAL_STATUSES:
        final_status = current_status

    record["maxHighDuringTracking"] = rounded(max_high)
    record["targetHitDate"] = target_hit_date
    record["stopLossHitDate"] = stop_loss_hit_date
    record["finalTrackingStatus"] = final_status
    record["currentProfitPct"] = rounded(
        (n((record.get("latestStatus") or {}).get("latestClose")) - entry)
        / entry
        * 100
        if entry
        else 0
    )
    record["maxProfitPct"] = rounded(
        (max_high - entry) / entry * 100 if entry and max_high else 0
    )
    progress = record.setdefault("progress", {})
    progress["targetHit"] = bool(target_hit_date)
    progress["stopBroken"] = bool(stop_loss_hit_date)
    progress["nearTarget"] = bool(target and max_high >= target * 0.9)
    progress["patternFailed"] = final_status == "FAILED"

    if final_status == "TARGET_HIT":
        target_end_date = target_hit_date or terminal_cutoff
        record["trackingStatus"] = "TARGET_HIT"
        record["trackingStatusText"] = STATUS_TEXT["TARGET_HIT"]
        record["endedAt"] = target_end_date
        record["endDate"] = target_end_date
        record["endReason"] = "追蹤期間曾達原始目標，完成追蹤"
        record["endPriority"] = "TARGET_FIRST"
        record["result"] = status_result("TARGET_HIT")
    elif final_status == "FAILED":
        stop_end_date = stop_loss_hit_date or terminal_cutoff
        record["trackingStatus"] = "FAILED"
        record["trackingStatusText"] = STATUS_TEXT["FAILED"]
        record["endedAt"] = stop_end_date
        record["endDate"] = stop_end_date
        record["endReason"] = "跌破原始停損，型態失敗"
        record["endPriority"] = (
            "STOP_FIRST" if stop_loss_hit_date else record.get("endPriority") or "FAILED"
        )
        record["result"] = status_result("FAILED")
        if target and max_high >= target * 0.9:
            append_system_note(
                record,
                stop_end_date,
                "追蹤期間曾接近原始目標，可檢討停利規則。",
            )
    elif final_status == "NEED_REVIEW":
        review_date = target_hit_date or stop_loss_hit_date or terminal_cutoff
        record["trackingStatus"] = "NEED_REVIEW"
        record["trackingStatusText"] = STATUS_TEXT["NEED_REVIEW"]
        record["endedAt"] = review_date
        record["endDate"] = review_date
        record["endReason"] = "同日觸發目標與停損，需人工確認"
        record["endPriority"] = "SAME_DAY_REVIEW"
        record["result"] = status_result("NEED_REVIEW")
    elif final_status == "EXPIRED":
        record["endPriority"] = record.get("endPriority") or "EXPIRED"
    elif final_status == "MANUAL_CLOSED":
        record["endPriority"] = record.get("endPriority") or "MANUAL_CLOSED"
    else:
        record["endPriority"] = None

    return record


def update_tracking_status(tracking_record, latest_market_data, market_regime, current_date=None):
    record = deepcopy(tracking_record)
    immutable_original = deepcopy(record.get("originalStrategy") or {})
    immutable_first_signal_date = record.get("firstSignalDate")
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
    record["latestStatus"] = latest
    record = evaluateTrackingLifecycle(
        record,
        [{"date": date, "high": high, "low": low, "close": close}],
    )
    lifecycle_status = record.get("finalTrackingStatus")

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
    target_hit = bool(record.get("targetHitDate"))
    lost_neckline_after_breakout = bool(
        previously_broke and neckline and close < neckline * 0.97
    )
    structural_failed = lost_neckline_after_breakout or is_true(row.get("patternInvalid"))
    pattern_failed = lifecycle_status == "FAILED" or structural_failed
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

    if lifecycle_status in {"TARGET_HIT", "FAILED", "NEED_REVIEW"}:
        status = lifecycle_status
    elif lifecycle_status in {"EXPIRED", "MANUAL_CLOSED"}:
        status = lifecycle_status
    elif near_target:
        status = "NEAR_TARGET"
    elif pullback_held:
        status = "PULLBACK_CONFIRM"
    elif previously_broke and close_held and not in_buy_zone:
        status = "IN_TREND"
    elif breakout_confirmed:
        status = "BREAKOUT_CONFIRMED"
    elif structural_failed:
        status = "FAILED"
    elif expired:
        status = "EXPIRED"
    elif in_buy_zone or (neckline and close >= neckline * 0.98):
        status = "WAIT_CONFIRM"
    else:
        status = "WATCHING"

    if status == "FAILED":
        latest["holderAction"] = "跌破原始停損或型態破壞，優先處理。"
        latest["cashAction"] = "取消觀察，等待新的主升段結構。"
        latest["riskWarning"] = "原始策略已失敗，不可用新的策略卡延後停損。"
    elif status == "TARGET_HIT":
        latest["holderAction"] = "已達原始第一目標，優先執行分批停利。"
        latest["cashAction"] = "目標已達成，空手者不追高。"
    elif status == "NEAR_TARGET":
        latest["holderAction"] = "接近原始第一目標，準備分批停利。"
        latest["cashAction"] = "接近目標，空手者不追高。"
    elif status == "EXPIRED":
        latest["holderAction"] = "原觀察策略已過期，重新檢查持股風險。"
        latest["cashAction"] = "移出進行中追蹤，等待新的完整結構。"
    elif status == "NEED_REVIEW":
        latest["holderAction"] = "同日觸及目標與停損，日 K 無法判斷先後，請人工核對。"
        latest["cashAction"] = "終局順序未明，不把此紀錄視為新進場訊號。"
        latest["riskWarning"] = "同日觸發目標與停損，需人工確認。"

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
        "stopBroken": bool(record.get("stopLossHitDate")),
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

    if status in {"TARGET_HIT", "FAILED", "NEED_REVIEW"} and lifecycle_status:
        pass
    elif status in ENDED_STATUSES:
        record["endDate"] = record.get("endDate") or date
        record["endedAt"] = record.get("endedAt") or date
        record["endReason"] = record.get("endReason") or STATUS_TEXT[status]
        record["endPriority"] = record.get("endPriority") or status
        record["result"] = record.get("result") or status_result(status)
    else:
        record["endDate"] = None
        record["endedAt"] = None
        record["endReason"] = ""
        record["endPriority"] = None
        record["result"] = ""
    record["originalStrategy"] = immutable_original
    record["firstSignalDate"] = immutable_first_signal_date
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
        "endedAt": None,
        "endReason": "",
        "endPriority": None,
        "maxHighDuringTracking": 0,
        "targetHitDate": None,
        "stopLossHitDate": None,
        "finalTrackingStatus": None,
        "currentProfitPct": 0,
        "maxProfitPct": 0,
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


def records_for_stock(records, market, code):
    return [
        record
        for record in records
        if record.get("market") == market
        and str(record.get("stockCode")) == str(code)
    ]


def snapshot_to_row(snapshot):
    grade = str(snapshot.get("grade") or "").upper()
    close = n(snapshot.get("close"))
    return {
        "date": snapshot.get("date"),
        "strategyAsOfDate": snapshot.get("date"),
        "code": snapshot.get("code"),
        "ticker": snapshot.get("code"),
        "name": snapshot.get("name"),
        "grade": grade,
        "score": 4 if grade == "A" else 3 if grade == "B" else 2,
        "safeScore": snapshot.get("safeScore"),
        "riskRewardRatio": snapshot.get("riskRewardRatio"),
        "riskPct": snapshot.get("riskPct"),
        "distanceFromNecklinePct": snapshot.get("distanceFromNecklinePct"),
        "close": close,
        "high": close,
        "low": close,
        "neckline": snapshot.get("neckline"),
        "observationEntry": pick(snapshot, "entry", default=close),
        "chaseRangeLow": snapshot.get("buyLow"),
        "chaseRangeHigh": snapshot.get("buyHigh"),
        "stopLoss": snapshot.get("stopLoss"),
        "target": snapshot.get("target"),
        "stage": pick(snapshot, "category", "pattern", default="舊版策略紀錄"),
        "originalSignal": pick(snapshot, "action", "category", default="舊版策略紀錄"),
        "adjustedSignal": pick(snapshot, "action", default=""),
        "strictOk": grade == "A",
        "forbiddenChase": grade == "C",
    }


def legacy_snapshot_entry(snapshot):
    return {
        "date": str(snapshot.get("date") or "")[:10],
        "market": snapshot.get("market"),
        "code": str(snapshot.get("code") or snapshot.get("ticker") or ""),
        "name": snapshot.get("name"),
        "grade": snapshot.get("grade"),
        "safeScore": snapshot.get("safeScore"),
        "riskRewardRatio": snapshot.get("riskRewardRatio"),
        "category": snapshot.get("category"),
        "close": snapshot.get("close"),
        "neckline": snapshot.get("neckline"),
        "buyLow": snapshot.get("buyLow"),
        "buyHigh": snapshot.get("buyHigh"),
        "stopLoss": snapshot.get("stopLoss"),
        "target": snapshot.get("target"),
        "action": snapshot.get("action"),
    }


def attach_legacy_snapshots(record, snapshots):
    notes = record.setdefault("notes", {"systemNotes": [], "manualNote": ""})
    existing = {
        str(item.get("date"))
        for item in notes.get("legacySnapshots", [])
        if isinstance(item, dict)
    }
    rows = [
        legacy_snapshot_entry(snapshot)
        for snapshot in snapshots
        if isinstance(snapshot, dict)
        and str(snapshot.get("date") or "") not in existing
    ]
    merged = [
        item
        for item in notes.get("legacySnapshots", [])
        if isinstance(item, dict)
    ] + rows
    notes["legacySnapshots"] = sorted(
        merged,
        key=lambda item: str(item.get("date") or ""),
        reverse=True,
    )[:60]
    if rows:
        latest_date = max(str(row.get("date") or "") for row in rows)
        append_system_note(
            record,
            latest_date,
            f"已匯入舊版近 5 日策略紀錄 {len(rows)} 筆，原始策略維持不變。",
        )


def migrate_legacy_snapshots(records, snapshots):
    if not isinstance(snapshots, dict):
        return records
    for key, raw_rows in snapshots.items():
        rows = sorted(
            [row for row in raw_rows if isinstance(row, dict) and row.get("date")]
            if isinstance(raw_rows, list)
            else [],
            key=lambda row: str(row.get("date") or ""),
        )
        if not rows:
            continue
        first = rows[0]
        market = str(first.get("market") or str(key).split(":", 1)[0])
        code = str(
            first.get("code")
            or first.get("ticker")
            or (str(key).split(":", 1)[1] if ":" in str(key) else "")
        )
        if not market or not code:
            continue
        matches = records_for_stock(records, market, code)
        if matches:
            target = min(
                matches,
                key=lambda record: str(record.get("firstSignalDate") or "9999-99-99"),
            )
        else:
            meta = {
                "strategyAsOfDate": first.get("date"),
                "marketRegime": "NO_DATA",
                "marketRegimeLabel": "舊資料匯入",
            }
            target = create_tracking_record(
                market,
                snapshot_to_row(first),
                meta,
                ["由舊版近 5 日策略紀錄建立追蹤，原始欄位以最早一筆保存。"],
            )
            for snapshot in rows[1:]:
                target = update_tracking_status(
                    target,
                    snapshot_to_row(snapshot),
                    "NO_DATA",
                    str(snapshot.get("date"))[:10],
                )
            records.append(target)
        attach_legacy_snapshots(target, rows)
    return records


def lock_to_row(lock):
    buy_low = n(lock.get("originalBuyLow"))
    buy_high = n(lock.get("originalBuyHigh"))
    watch = (buy_low + buy_high) / 2 if buy_low and buy_high else buy_low or buy_high
    close = n(lock.get("currentPrice"), watch)
    return {
        "date": str(pick(lock, "updatedAt", "createdAt", default=""))[:10],
        "strategyAsOfDate": str(pick(lock, "updatedAt", "createdAt", default=""))[:10],
        "code": lock.get("code"),
        "ticker": lock.get("code"),
        "name": lock.get("name"),
        "score": 3,
        "grade": "B",
        "close": close,
        "high": close,
        "low": close,
        "neckline": lock.get("originalNeckline"),
        "observationEntry": watch,
        "chaseRangeLow": buy_low,
        "chaseRangeHigh": buy_high,
        "stopLoss": lock.get("originalStopLoss"),
        "target": lock.get("originalTarget"),
        "stage": pick(lock, "originalStage", default="舊版策略鎖定"),
        "originalSignal": pick(
            lock,
            "trackingStatus",
            "originalStage",
            default="舊版策略鎖定",
        ),
        "holderAction": lock.get("holderAction"),
        "emptyAction": lock.get("emptyAction"),
        "forbiddenChase": False,
    }


def legacy_lock_entry(lock):
    return {
        "strategyDate": str(lock.get("createdAt") or "")[:10],
        "market": lock.get("market"),
        "code": str(lock.get("code") or ""),
        "name": lock.get("name"),
        "originalStage": lock.get("originalStage"),
        "originalSignal": pick(
            lock,
            "trackingStatus",
            "originalStage",
            default="舊版策略鎖定",
        ),
        "originalNeckline": lock.get("originalNeckline"),
        "originalBuyLow": lock.get("originalBuyLow"),
        "originalBuyHigh": lock.get("originalBuyHigh"),
        "originalStopLoss": lock.get("originalStopLoss"),
        "originalTarget": lock.get("originalTarget"),
        "legacyStatus": lock.get("status"),
        "trackingStatus": lock.get("trackingStatus"),
        "updatedAt": lock.get("updatedAt"),
    }


def attach_legacy_lock(record, lock):
    notes = record.setdefault("notes", {"systemNotes": [], "manualNote": ""})
    if not notes.get("legacyLock"):
        notes["legacyLock"] = legacy_lock_entry(lock)
        append_system_note(
            record,
            str(pick(lock, "updatedAt", "createdAt", default=""))[:10],
            "已匯入舊版策略鎖定資料，原始策略維持不變。",
        )


def migrate_legacy_locks(records, locks):
    if not isinstance(locks, dict):
        return records
    for lock in locks.values():
        if not isinstance(lock, dict):
            continue
        market = str(lock.get("market") or "")
        code = str(lock.get("code") or "")
        if not market or not code:
            continue
        matches = records_for_stock(records, market, code)
        if matches:
            target = min(
                matches,
                key=lambda record: str(record.get("firstSignalDate") or "9999-99-99"),
            )
        else:
            row = lock_to_row(lock)
            target = create_record_from_lock(
                market,
                lock,
                row,
                {
                    "strategyAsOfDate": lock.get("createdAt"),
                    "marketRegime": "NO_DATA",
                    "marketRegimeLabel": "舊資料匯入",
                },
            )
            records.append(target)
        attach_legacy_lock(target, lock)
    return records


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
            append_system_note(active, current_date, NEW_SIGNAL_NOTICE)
            continue
        ended = newest_ended_record(records, market, code)
        if ended and str(ended.get("endDate")) >= current_date:
            continue
        records.append(create_tracking_record(market, row, meta, reasons))
    return records


def process_tracking(
    existing,
    tw_payload,
    us_payload,
    locks=None,
    snapshots=None,
    historical_prices=None,
):
    existing = existing if isinstance(existing, dict) else {}
    records = [
        deepcopy(record)
        for record in existing.get("records", [])
        if isinstance(record, dict) and record.get("trackingId")
    ]
    for record in records:
        record.setdefault("endedAt", record.get("endDate"))
        record.setdefault("endPriority", None)
        record.setdefault("maxHighDuringTracking", 0)
        record.setdefault("targetHitDate", None)
        record.setdefault("stopLossHitDate", None)
        record.setdefault("finalTrackingStatus", None)
        record.setdefault("notes", {"systemNotes": [], "manualNote": ""})
    records = migrate_legacy_snapshots(records, snapshots)
    records = migrate_legacy_locks(records, locks)
    historical_prices = historical_prices if isinstance(historical_prices, dict) else {}
    records = [
        evaluateTrackingLifecycle(
            record,
            tracking_lifecycle_prices(
                record,
                historical_prices.get(
                    f"{record.get('market')}:{record.get('stockCode')}",
                    [],
                ),
            ),
        )
        for record in records
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
            "version": "strategy-tracking-v3",
            "activeCount": active_count,
            "endedCount": ended_count,
            "maxWaitingTradingDays": 15,
            "legacyLocksImported": sum(
                bool((record.get("notes") or {}).get("legacyLock"))
                for record in records
            ),
            "legacySnapshotStocksImported": sum(
                bool((record.get("notes") or {}).get("legacySnapshots"))
                for record in records
            ),
            "lifecycleHistoriesEvaluated": sum(
                bool(
                    historical_prices.get(
                        f"{record.get('market')}:{record.get('stockCode')}"
                    )
                )
                for record in records
            ),
        },
        "records": records,
    }


def main():
    DATA_DIR.mkdir(exist_ok=True)
    existing = read_json(TRACKING_PATH, {"meta": {}, "records": []})
    tw_payload = read_json(TW_PATH, {"meta": {}, "stocks": []})
    us_payload = read_json(US_PATH, {"meta": {}, "stocks": []})
    locks = read_json(LOCKS_PATH, {})
    snapshots = read_json(SNAPSHOTS_PATH, {})
    if not isinstance(locks, dict):
        locks = {}
    if not isinstance(snapshots, dict):
        snapshots = {}
    historical_prices = download_tracking_histories(existing)
    tw_payload = supplement_active_tracking_rows(existing, "台股", tw_payload)
    us_payload = supplement_active_tracking_rows(existing, "美股", us_payload)
    output = process_tracking(
        existing,
        tw_payload,
        us_payload,
        locks,
        snapshots,
        historical_prices,
    )
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
