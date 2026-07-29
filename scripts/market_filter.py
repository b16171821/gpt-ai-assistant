import json
import math


REGIME_INFO = {
    "ATTACK": {
        "label": "進攻盤",
        "description": "可正常尋找主升段",
        "strategy": "個股主升段訊號維持原本判斷，可產生正式進場名單。",
    },
    "WATCH": {
        "label": "觀察盤",
        "description": "買點降級，不追價",
        "strategy": "大盤未創高，不追價，只等回踩不破或收盤確認。",
    },
    "DEFENSE": {
        "label": "防守盤",
        "description": "停止積極進場，持股啟動風控",
        "strategy": "停止新進場，持股啟動停利 / 停損，主升段名單降級觀察。",
    },
    "STOP": {
        "label": "停止進場",
        "description": "只觀察，不開新倉",
        "strategy": "停止新進場，先保留現金，等待大盤止跌。",
    },
}


def sf(value, default=0.0):
    try:
        if value is None:
            return default
        if hasattr(value, "item"):
            value = value.item()
        if math.isnan(float(value)):
            return default
        return float(value)
    except Exception:
        return default


def sr(value, digits=2):
    value = sf(value)
    return round(value, digits) if math.isfinite(value) else 0


def _bool(value):
    return bool(value)


def _tail_value(series, offset=1, default=0.0):
    try:
        if len(series) < offset:
            return default
        return sf(series.iloc[-offset], default)
    except Exception:
        return default


def analyze_index(df, name="加權指數", support=0):
    if df is None or getattr(df, "empty", True):
        return {"name": name, "status": "資料不足", "isWeak": True, "dataOk": False}
    x = df.dropna(subset=["Close"]).copy()
    if len(x) < 30:
        return {"name": name, "status": "資料不足", "isWeak": True, "dataOk": False}

    x["MA5"] = x["Close"].rolling(5).mean()
    x["MA10"] = x["Close"].rolling(10).mean()
    x["MA20"] = x["Close"].rolling(20).mean()
    x["AVG_VOLUME20"] = x["Volume"].rolling(20).mean() if "Volume" in x else 0

    latest = x.iloc[-1]
    prev = x.iloc[-2] if len(x) >= 2 else latest
    close = sf(latest.get("Close"))
    high = sf(latest.get("High"), close)
    low = sf(latest.get("Low"), close)
    open_ = sf(latest.get("Open"), close)
    volume = sf(latest.get("Volume"))
    avg_volume20 = sf(latest.get("AVG_VOLUME20"))
    ma5 = sf(latest.get("MA5"))
    ma10 = sf(latest.get("MA10"))
    ma20 = sf(latest.get("MA20"))
    prev_ma5 = sf(prev.get("MA5"))
    prev_ma20 = sf(prev.get("MA20"))
    prior = x.iloc[:-1]
    prior_high = sf(prior["High"].tail(60).max()) if "High" in prior and len(prior) else 0
    recent_high = max(prior_high, high)
    recent_low = sf(prior["Low"].tail(20).min()) if "Low" in prior and len(prior) else 0
    recent_n_high = sf(x["High"].tail(5).max()) if "High" in x else high
    close_to_high_pct = (recent_high - close) / recent_high * 100 if recent_high else 999
    close_above_ma5 = close > ma5 if ma5 else False
    close_above_ma10 = close > ma10 if ma10 else False
    close_above_ma20 = close > ma20 if ma20 else False
    ma5_up = ma5 > prev_ma5 if prev_ma5 else False
    ma20_up = ma20 > prev_ma20 if prev_ma20 else False
    ma20_flat_or_down = ma20 <= prev_ma20 * 1.001 if prev_ma20 else False
    new_high = bool(prior_high and (high >= prior_high or close >= prior_high * 0.997))
    near_recent_high = close_to_high_pct <= 3
    failed_new_high = bool(
        prior_high
        and close < prior_high
        and recent_n_high < prior_high * 1.001
        and (close < ma5 or close < ma10)
        and ma5 < prev_ma5
    )
    lower_low = bool(recent_low and close < recent_low)
    lower_lows = False
    if "Low" in x and len(x) >= 3:
        lows = list(x["Low"].tail(3).astype(float))
        lower_lows = lows[2] < lows[1] < lows[0]
    long_black = bool(close < open_ and close > 0 and (open_ - close) / close >= 0.025 and (not avg_volume20 or volume >= avg_volume20 * 1.2))
    support_broken = bool(support and recent_high >= support * 1.03 and close < support)
    is_weak = bool((not close_above_ma20) or (not ma5_up and ma20_flat_or_down) or failed_new_high or lower_low)
    status = "創高強勢" if new_high else ("接近高點" if near_recent_high else ("跌破月線" if not close_above_ma20 else "高檔震盪"))

    return {
        "name": name,
        "date": str(x.index[-1].date()) if hasattr(x.index[-1], "date") else str(x.index[-1]),
        "status": status,
        "close": sr(close),
        "ma5": sr(ma5),
        "ma10": sr(ma10),
        "ma20": sr(ma20),
        "recentHigh": sr(recent_high),
        "closeToHighPct": sr(close_to_high_pct),
        "newHigh": _bool(new_high),
        "nearRecentHigh": _bool(near_recent_high),
        "closeAboveMA5": _bool(close_above_ma5),
        "closeAboveMA10": _bool(close_above_ma10),
        "closeAboveMA20": _bool(close_above_ma20),
        "ma5Up": _bool(ma5_up),
        "ma20Up": _bool(ma20_up),
        "ma20FlatOrDown": _bool(ma20_flat_or_down),
        "failedNewHigh": _bool(failed_new_high),
        "lowerLow": _bool(lower_low),
        "lowerLows": _bool(lower_lows),
        "longBlack": _bool(long_black),
        "supportBroken": _bool(support_broken),
        "isWeak": _bool(is_weak),
        "dataOk": True,
    }


def classify_market_regime(weighted_df, otc_df=None, support=40000, strong_stop_break_rate=0, a_disappear_rate=0):
    weighted = analyze_index(weighted_df, "加權指數", support=support)
    otc = analyze_index(otc_df, "櫃買指數") if otc_df is not None else {"name": "櫃買指數", "status": "未提供", "isWeak": False, "dataOk": False}
    data_ok = bool(weighted.get("dataOk"))
    if not data_ok:
        regime = "WATCH"
        reasons = ["大盤資料不足，先降級觀察"]
    else:
        otc_weak = bool(otc.get("isWeak")) if otc.get("dataOk") else False
        attack_ok = (
            (weighted.get("newHigh") or weighted.get("nearRecentHigh"))
            and weighted.get("closeAboveMA5")
            and weighted.get("closeAboveMA10")
            and weighted.get("closeAboveMA20")
            and weighted.get("ma5Up")
            and weighted.get("ma20Up")
            and not otc_weak
            and strong_stop_break_rate < 0.25
        )
        stop_hit = (
            weighted.get("supportBroken")
            or weighted.get("lowerLows")
            or weighted.get("longBlack")
            or (weighted.get("isWeak") and otc_weak)
            or a_disappear_rate >= 0.5
        )
        defense_hit = (
            (not weighted.get("closeAboveMA20"))
            or (not weighted.get("ma5Up") and weighted.get("ma20FlatOrDown"))
            or (weighted.get("failedNewHigh") and (weighted.get("ma20FlatOrDown") or weighted.get("lowerLow")))
            or strong_stop_break_rate >= 0.25
            or otc_weak
        )
        if stop_hit:
            regime = "STOP"
        elif defense_hit:
            regime = "DEFENSE"
        elif attack_ok:
            regime = "ATTACK"
        else:
            regime = "WATCH"
        reasons = []
        if weighted.get("newHigh"):
            reasons.append("加權創近期新高")
        elif weighted.get("nearRecentHigh"):
            reasons.append("加權接近近期高點 3% 內")
        else:
            reasons.append("加權未再創高")
        if weighted.get("closeAboveMA5") and weighted.get("closeAboveMA10") and weighted.get("closeAboveMA20"):
            reasons.append("收盤站上 5MA / 10MA / 20MA")
        else:
            reasons.append("收盤跌破短均線或 20MA")
        if weighted.get("ma5Up") and weighted.get("ma20Up"):
            reasons.append("5MA、20MA 向上")
        else:
            reasons.append("短均線動能轉弱")
        if otc.get("dataOk"):
            reasons.append("櫃買未明顯轉弱" if not otc_weak else "櫃買同步轉弱")
        if strong_stop_break_rate >= 0.25:
            reasons.append("強勢股停損破線比例偏高")
        if a_disappear_rate >= 0.5:
            reasons.append("A級名單大量消失")

    no_new_high_warning = bool(weighted.get("failedNewHigh"))
    warnings = []
    if no_new_high_warning:
        warnings.append("大盤未再創高，短線動能轉弱。主升段買點全部降級，避免在轉弱位置追價。")
    if regime in {"DEFENSE", "STOP"}:
        warnings.append("大盤轉弱，主升段股容易補跌。")

    info = REGIME_INFO[regime]
    return {
        "marketRegime": regime,
        "regimeLabel": info["label"],
        "description": info["description"],
        "strategy": info["strategy"],
        "reasons": reasons,
        "warnings": warnings,
        "noNewHighWarning": no_new_high_warning,
        "weighted": weighted,
        "otc": otc,
    }


def first_target_progress(row, lock=None):
    target = sf((lock or {}).get("originalTarget")) or sf(row.get("target"))
    close = sf(row.get("close"))
    return close / target * 100 if close and target else 0


def original_signal(row):
    if row.get("nearTarget") or first_target_progress(row) >= 90:
        return "接近目標"
    stage = str(row.get("stage") or row.get("category") or row.get("status") or "")
    if "回踩" in stage:
        return "回踩買點"
    if "突破" in stage:
        return "突破買點"
    if row.get("strictOk") or row.get("grade") == "A":
        return "正式進場"
    return "主升段續抱" if row.get("holding") else "觀察"


def apply_market_filter(row, market_filter, lock=None):
    regime = (market_filter or {}).get("marketRegime", "ATTACK")
    label = REGIME_INFO.get(regime, REGIME_INFO["ATTACK"])["label"]
    signal = row.get("originalSignal") or original_signal(row)
    holding = bool(row.get("holding") or lock)
    progress = first_target_progress(row, lock)
    change_pct = sf(row.get("changePct") or row.get("dailyChangePct"))
    near_limit_up = bool(row.get("nearLimitUp")) or change_pct >= 8
    adjusted = signal
    holder = "續抱但盯緊頸線、停損與目標。"
    cash = "依原本條件觀察，不追超過買入觀察區。"
    warnings = []

    if regime == "WATCH":
        if signal == "突破買點":
            adjusted = "觀察，不追價"
            cash = "大盤未創高，突破買點降級；只等收盤確認或回踩不破。"
        elif signal == "回踩買點":
            adjusted = "小部位試單觀察"
            cash = "只觀察回踩是否守住，不追價。"
        elif signal == "正式進場":
            adjusted = "等待收盤確認"
            cash = "正式進場降級為等待確認。"
        holder = "續抱但提高停利警戒，跌破短線支撐要處理。"
        warnings.append("大盤未創高，不追價，只等回踩不破。")
    elif regime == "DEFENSE":
        if signal == "突破買點":
            adjusted = "取消進場，降級觀察"
        elif signal == "回踩買點":
            adjusted = "僅觀察，不進場"
        elif signal == "正式進場":
            adjusted = "降級觀察"
        cash = "不追價，等待大盤止跌後再觀察。"
        holder = "啟動移動停利；若跌破原始停損優先處理，接近目標分批停利。"
        warnings.append("大盤轉弱，主升段股容易補跌。")
    elif regime == "STOP":
        adjusted = "取消新進場，只保留觀察"
        cash = "停止新進場，先保留現金，等待大盤止跌。"
        holder = "只看續抱 / 減碼 / 停利 / 停損，不新增部位。"
        warnings.append("停止新進場，禁止顯示今日可進場。")
    elif regime == "ATTACK" and signal == "突破買點" and (row.get("strictOk") or row.get("grade") == "A"):
        adjusted = "正式進場"

    if holding and near_limit_up and regime != "ATTACK":
        warnings.append("個股強，但大盤轉弱。漲停不是加碼點，優先分批停利，避免隔日補跌。")
        if progress >= 95:
            holder = "已達第一目標 95% 以上，至少停利 1/2；可考慮鎖利，不貪最後一段。"
        elif progress >= 90:
            holder = "已達第一目標 90% 以上，至少停利 1/3；漲停不加碼。"
        else:
            holder = "漲停但大盤轉弱，可考慮先鎖利一部分，不加碼。"

    original_stop = sf((lock or {}).get("originalStopLoss"))
    if holding and original_stop and sf(row.get("close")) and sf(row.get("close")) < original_stop:
        warnings.append("既有持股跌破原始停損，隔天第一件事處理；不可用新策略卡把停損往下移。")

    if holding and lock:
        warnings.append("此為新觀察策略，僅適用空手者。既有持股仍以原始策略停損與目標為準。")

    row["marketRegime"] = regime
    row["marketRegimeImpact"] = f"{regime} {label}"
    row["originalSignal"] = signal
    row["adjustedSignal"] = adjusted
    row["holderAction"] = holder
    row["cashAction"] = cash
    row["riskWarning"] = "；".join(dict.fromkeys(warnings)) if warnings else "依原本個股策略執行，仍不可追高。"
    row["marketFilterAllowsEntry"] = regime == "ATTACK"
    row["profitTakingAlert"] = "個股強，但大盤轉弱" in row["riskWarning"]
    return row


def apply_market_filter_to_rows(rows, market_filter, locks=None):
    locks = locks or {}
    out = []
    for row in rows:
        key = row.get("lockKey") or row.get("code") or row.get("ticker") or row.get("name")
        lock = locks.get(key)
        out.append(apply_market_filter(row, market_filter, lock=lock))
    return out


def previous_a_count_from_snapshots(path, market):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    count = 0
    for key, items in payload.items():
        if not str(key).startswith(f"{market}:") or not isinstance(items, list) or not items:
            continue
        latest = sorted(items, key=lambda x: str(x.get("date", "")), reverse=True)[0]
        if latest.get("grade") == "A":
            count += 1
    return count


def apply_breadth_guard(market_filter, rows, previous_a_count=0):
    strong = [
        r for r in rows
        if r.get("grade") in {"A", "B"} or r.get("strictOk") or sf(r.get("score")) >= 3
    ]
    broken = [
        r for r in strong
        if sf(r.get("close")) and sf(r.get("stopLoss")) and sf(r.get("close")) < sf(r.get("stopLoss"))
    ]
    rate = len(broken) / len(strong) if strong else 0
    out = dict(market_filter or {})
    out["strongStopBreakRate"] = sr(rate * 100)
    out["strongStopBreakCount"] = len(broken)
    out["strongWatchCount"] = len(strong)
    current_a_count = sum(1 for r in rows if r.get("grade") == "A")
    disappear_rate = (previous_a_count - current_a_count) / previous_a_count if previous_a_count >= 3 else 0
    out["previousACount"] = previous_a_count
    out["currentACount"] = current_a_count
    out["aDisappearRate"] = sr(max(0, disappear_rate) * 100)
    warnings = list(out.get("warnings") or [])
    reasons = list(out.get("reasons") or [])
    if (rate >= 0.5 or disappear_rate >= 0.5) and out.get("marketRegime") != "STOP":
        out.update({
            "marketRegime": "STOP",
            "regimeLabel": REGIME_INFO["STOP"]["label"],
            "description": REGIME_INFO["STOP"]["description"],
            "strategy": REGIME_INFO["STOP"]["strategy"],
        })
        if rate >= 0.5:
            reasons.append("系統內強勢股大量跌破停損")
            warnings.append("A/B 強勢股停損破線比例過高，停止新進場。")
        if disappear_rate >= 0.5:
            reasons.append("A級名單大量消失")
            warnings.append("系統內 A 級名單大量消失，停止新進場。")
    elif rate >= 0.25 and out.get("marketRegime") not in {"DEFENSE", "STOP"}:
        out.update({
            "marketRegime": "DEFENSE",
            "regimeLabel": REGIME_INFO["DEFENSE"]["label"],
            "description": REGIME_INFO["DEFENSE"]["description"],
            "strategy": REGIME_INFO["DEFENSE"]["strategy"],
        })
        reasons.append("強勢股開始補跌")
        warnings.append("強勢股停損破線比例偏高，主升段買點降級。")
    out["warnings"] = list(dict.fromkeys(warnings))
    out["reasons"] = list(dict.fromkeys(reasons))
    return out

