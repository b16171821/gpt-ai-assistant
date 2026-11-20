"""Causal short-side patterns. Pure functions; no long-side imports or writes."""
from copy import deepcopy
from statistics import mean

from .config import CONFIG, ENTRY_SIGNALS, SIGNAL_LABELS
from .validation import ShortDataError, positive, price_metrics, validate_bars


def average(values, count):
    return sum(values[-count:])/count if len(values) >= count else None


def score_quality(trend, signal, volume_ratio, metrics):
    parts = {'trend': 25 if trend == 'CONFIRMED' else 15 if trend == 'FORMING' else 0,
             'structure': 25 if signal in {'FAILED_RETEST', 'MA_REJECTION'} else 20 if signal == 'PLATFORM_BREAK' else 10 if signal == 'BEAR_TREND' else 0,
             'volume': 15 if volume_ratio >= 1.5 else 8 if volume_ratio >= 1 else 0,
             'stopDistance': 0, 'riskReward': 0}
    if not metrics['validPrices']:
        return None, parts
    risk, rr = metrics['riskPct'], metrics['riskRewardRatio']
    parts['stopDistance'] = 15 if 0 < risk <= 4 else 8 if 4 < risk <= 6 else 0
    parts['riskReward'] = 20 if rr >= 3 else 12 if rr >= 2 else 0
    return sum(parts.values()), parts


def analyze_history(bars, meta=None, config=CONFIG, *, include_charts=True):
    meta = meta or {}
    issues = validate_bars(bars)
    if issues:
        raise ShortDataError(';'.join(issues[:10]))
    history, closes, fasts, slows, atrs, pivoth, pivotl, output = [], [], [], [], [], [], [], []
    true_ranges, trend_streak, breakdown, ma_bounce, last_action = [], 0, None, None, -1000
    for i, raw in enumerate(bars):
        current = deepcopy(raw)
        # Apply actions only on/after their effective session. Never use a future
        # adjusted-close column to change an earlier signal. Raw bars remain intact.
        ratio, dividend = raw.get('split', 0), raw.get('dividend', 0)
        if ratio or dividend:
            ratio = ratio or 1
            for old in history:
                for key in ('open', 'high', 'low', 'close'):
                    old[key] = old[key] / ratio - dividend
                old['volume'] *= ratio
            closes = [x['close'] for x in history]
            fasts = [v/ratio-dividend if v is not None else None for v in fasts]
            slows = [v/ratio-dividend if v is not None else None for v in slows]
            atrs = [v/ratio if v is not None else None for v in atrs]
            true_ranges = [v/ratio for v in true_ranges]
            pivoth = [(j, p/ratio-dividend) for j, p in pivoth]
            pivotl = [(j, p/ratio-dividend) for j, p in pivotl]
            breakdown, ma_bounce, trend_streak, last_action = None, None, 0, i
        previous = history[-1] if history else None
        tr = max(current['high']-current['low'], abs(current['high']-previous['close']), abs(current['low']-previous['close'])) if previous else current['high']-current['low']
        prior_atr = atrs[-1] if atrs else None
        true_ranges.append(tr)
        atr = (prior_atr*(config.atr_period-1)+tr)/config.atr_period if prior_atr is not None else average(true_ranges, config.atr_period)
        history.append(current)
        closes.append(current['close'])
        fast, slow = average(closes, config.ma_fast), average(closes, config.ma_slow)
        fasts.append(fast); slows.append(slow); atrs.append(atr)
        side = config.pivot_side
        if i >= side*2:
            j = i-side
            window = history[j-side:j+side+1]
            center = history[j]
            if center['high'] > max(x['high'] for k,x in enumerate(window) if k != side):
                pivoth.append((j, center['high']))
            if center['low'] < min(x['low'] for k,x in enumerate(window) if k != side):
                pivotl.append((j, center['low']))
        pivoth=[p for p in pivoth if p[0]>=i-120]
        pivotl=[p for p in pivotl if p[0]>=i-120]
        if i+1 < config.min_history or not positive(atr) or not positive(prior_atr):
            continue
        c, o, h, low = (current[k] for k in ('close', 'open', 'high', 'low'))
        fast_before, slow_before = fasts[i-config.slope_days], slows[i-config.slope_days]
        base_trend = c < fast < slow and fast_before is not None and fast < fast_before
        falling = base_trend and slow_before is not None and slow < slow_before
        waves = len(pivoth)>=2 and len(pivotl)>=2 and pivoth[-1][1] < pivoth[-2][1] and pivotl[-1][1] < pivotl[-2][1]
        trend_streak = trend_streak+1 if falling and waves else 0
        trend = 'CONFIRMED' if trend_streak >= config.trend_confirm_days else 'FORMING' if base_trend else 'NONE'
        avg_vol = sum(x['volume'] for x in history[-21:-1])/20
        vr = current['volume']/avg_vol if avg_vol else 0
        prior = history[-config.platform_days-1:-1]
        bottom, top = min(x['low'] for x in prior), max(x['high'] for x in prior)
        touch_indices = [k for k,x in enumerate(prior) if x['low'] <= bottom+prior_atr*config.touch_atr]
        spaced = []
        for k in touch_indices:
            if not spaced or k-spaced[-1]>=config.touch_spacing:
                spaced.append(k)
        platform_ok = positive(bottom) and (top-bottom)/bottom <= config.platform_max_width and len(spaced)>=config.platform_touches
        location = (c-low)/(h-low) if h>low else 1
        broke = platform_ok and c < bottom-config.break_atr*prior_atr and vr>=config.break_volume and location<=config.close_location_max
        raw_signals, witnesses = [], {}
        if trend == 'CONFIRMED':
            raw_signals.append('BEAR_TREND')
        false_break, retest_confirm, ma_confirm = False, False, False
        if breakdown:
            age = i-breakdown['index']
            if 0 < age <= config.false_break_days and c >= breakdown['level']+config.reclaim_atr*breakdown['atr']:
                false_break = True
                breakdown['invalid'] = True
                raw_signals.append('FALSE_BREAK')
            if 0 < age <= config.retest_days and not breakdown.get('invalid'):
                bounce = breakdown.get('bounce')
                if bounce and i>bounce['index'] and i-bounce['index']<=config.rejection_days and c<bounce['low']-config.rejection_atr*breakdown['atr'] and c<o and not bounce.get('confirmed'):
                    retest_confirm = True
                    bounce['confirmed'] = True
                    raw_signals.append('FAILED_RETEST')
                    witnesses['retest'] = {'breakDate':breakdown['date'], 'reboundDate':bounce['date'], 'confirmedDate':raw['date']}
                if not bounce and previous and c>previous['close'] and abs(h-breakdown['level'])<=config.touch_atr*breakdown['atr'] and c<breakdown['level']+config.reclaim_atr*breakdown['atr']:
                    breakdown['bounce']={'index':i,'date':raw['date'],'low':low,'high':h}
        if ma_bounce:
            age = i-ma_bounce['index']
            if age > config.rejection_days or c>ma_bounce['level']+config.reclaim_atr*ma_bounce['atr']:
                ma_bounce = None
            elif age>0 and c<ma_bounce['low']-config.rejection_atr*ma_bounce['atr'] and c<o and base_trend and not ma_bounce.get('confirmed'):
                ma_confirm = True
                ma_bounce['confirmed'] = True
                raw_signals.append('MA_REJECTION')
                witnesses['maRejection']={'reboundDate':ma_bounce['date'],'confirmedDate':raw['date'],'maKnownAt':ma_bounce['maDate']}
        if not ma_bounce and base_trend and previous and c>previous['close'] and all(history[i-k]['close']<fasts[i-k] for k in (1,2,3)) and abs(h-fasts[i-1])<=config.touch_atr*prior_atr and c<=fasts[i-1]+config.reclaim_atr*prior_atr:
            ma_bounce={'index':i,'date':raw['date'],'level':fasts[i-1],'maDate':bars[i-1]['date'],'low':low,'high':h,'atr':prior_atr}
        # Do not overwrite an in-progress retest with today's unrelated lower low.
        if broke:
            raw_signals.append('PLATFORM_BREAK')
            if not breakdown or breakdown.get('invalid') or i-breakdown['index']>config.retest_days:
                breakdown={'index':i,'date':raw['date'],'level':bottom,'top':top,'atr':prior_atr,'referenceEnd':bars[i-1]['date']}
        signal = next((x for x in ('FAILED_RETEST','MA_REJECTION','PLATFORM_BREAK','BEAR_TREND') if x in raw_signals),'NO_SETUP')
        reference, ref_atr, reference_end = (breakdown['level'], breakdown['atr'], breakdown['referenceEnd']) if breakdown and not breakdown.get('invalid') else (fasts[i-1],prior_atr,bars[i-1]['date'])
        ret5 = c/closes[-config.deep_return_days-1]-1
        deep = (reference-c)/ref_atr>config.deep_break_atr or (fast-c)/atr>config.deep_ma_atr or ret5<=config.deep_return
        if deep:
            raw_signals.append('TOO_EXTENDED')
        if false_break:
            original_signal = 'FALSE_BREAK'
        else:
            original_signal = signal
        stop = reference+config.break_stop_atr*ref_atr
        if signal == 'FAILED_RETEST' and breakdown and breakdown.get('bounce'):
            stop=max(reference,breakdown['bounce']['high'])+config.rejection_stop_atr*ref_atr
        elif signal == 'PLATFORM_BREAK':
            reference,ref_atr,reference_end=bottom,prior_atr,bars[i-1]['date']
            stop=reference+config.break_stop_atr*ref_atr
        elif signal == 'MA_REJECTION' and ma_bounce:
            reference, ref_atr = ma_bounce['level'],ma_bounce['atr']
            reference_end=ma_bounce['maDate']
            stop=max(reference,ma_bounce['high'])+config.rejection_stop_atr*ref_atr
        entry, zone_low, zone_high = c,c-config.zone_atr*ref_atr,c+config.zone_atr*ref_atr
        supports = [p for j,p in pivotl if j>=i-120 and 0<p<zone_low]
        if signal in {'PLATFORM_BREAK','FAILED_RETEST'}:
            measured=(bottom-(top-bottom)) if signal=='PLATFORM_BREAK' else (breakdown['level']-(breakdown['top']-breakdown['level'])) if breakdown else None
            if positive(measured) and measured<zone_low:
                supports.append(measured)
        target=max(supports) if supports else None
        metrics=price_metrics(entry,stop,target)
        if not (positive(zone_low) and stop>zone_high and positive(target) and target<zone_low):
            metrics={'validPrices':False,'riskPct':None,'rewardPct':None,'riskRewardRatio':None}
        quality, parts=score_quality(trend,signal,vr,metrics)
        reasons=[]
        if trend!='CONFIRMED': reasons.append('空頭趨勢尚未完成確認')
        if signal not in ENTRY_SIGNALS: reasons.append('尚未完成進場型態確認')
        if deep: reasons.append('已跌深，禁止追空')
        if false_break: reasons.append('站回原支撐，疑似假跌破')
        if not metrics['validPrices']: reasons.append('價位或下方目標不足')
        elif metrics['riskPct']>config.maximum_stop_pct: reasons.append('停損距離超過6%')
        elif metrics['riskRewardRatio']<config.minimum_rr: reasons.append('風報比未達2')
        action_pending=i-last_action<config.action_cooldown_days
        if action_pending: reasons.append('公司行動後觀察期')
        if current['volume']<=0: reasons.append('無有效成交量')
        if quality is None or quality<config.priority_score: reasons.append('技術品質未達80分')
        technical_candidate=not reasons
        adjusted='禁止追空' if deep else '疑似假跌破' if false_break else '技術條件確認' if technical_candidate else '等待確認'
        row={**meta,'direction':'SHORT','strategyVersion':config.version,'dataDate':raw['date'],
             'date':raw['date'],'priceType':'COMPLETE_CLOSE','latestClose':raw['close'],'close':raw['close'],
             'high':raw['high'],'low':raw['low'],'volume':raw['volume'],'ma20':fast,'ma60':slow,'atr14':atr,
             'stage':SIGNAL_LABELS[original_signal],'originalSignal':original_signal,'rawSignals':raw_signals or ['NO_SETUP'],
             'adjustedSignal':adjusted,'statusReason':'；'.join(reasons) if reasons else '技術條件完整，交易資格仍須獨立核實',
             'pressure':reference,'neckline':reference,'referenceKnownAt':reference_end,
             'observationLow':zone_low,'observationHigh':zone_high,'entry':entry,'entryBasis':'OBSERVATION_MIDPOINT',
             'stopLoss':stop,'target':target,**metrics,'qualityScore':quality,'qualityParts':parts,
             'technicalCandidate':technical_candidate,'technicalGrade':'A' if technical_candidate else 'B' if quality is not None and quality>=config.reserve_score else 'C',
             'trendState':trend,'volumeRatio':vr,'tooExtended':deep,'falseBreak':false_break,
             'corporateActionPending':action_pending,'signalWitnesses':witnesses,
             'averageTurnover20':sum(x['close']*x['volume'] for x in history[-20:])/20,
             'averageVolume20':sum(x['volume'] for x in history[-20:])/20,
             'chart':[{'date':bars[k]['date'],'close':history[k]['close']} for k in range(max(0,i-39),i+1)] if include_charts and i==len(bars)-1 else [],
             'dataIssues':[]}
        output.append(row)
    return output


def market_environment(index_series, as_of, config=CONFIG):
    evidence=[]
    for name, bars in index_series.items():
        if validate_bars(bars,as_of=as_of) or len(bars)<config.ma_slow+config.slope_days:
            return {'code':'NO_DATA','label':'資料不足，僅供觀察','asOfDate':as_of,'allowEntry':False,'indexes':evidence}
        closes=[x['close'] for x in bars]
        fast,slow=average(closes,20),average(closes,60)
        before=average(closes[:-config.slope_days],20)
        evidence.append({'name':name,'date':bars[-1]['date'],'close':closes[-1],'ma20':fast,'ma60':slow,
                         'bear':closes[-1]<fast<slow and fast<before,'bull':closes[-1]>fast>slow and fast>before})
    code='NO_DATA' if len(evidence)<2 else 'FAVORABLE' if all(x['bear'] for x in evidence) else 'BULL_STRONG' if all(x['bull'] for x in evidence) else 'WAIT'
    labels={'NO_DATA':'資料不足，僅供觀察','FAVORABLE':'空方環境有利','BULL_STRONG':'多方強勢，避免逆勢放空','WAIT':'等待確認'}
    return {'code':code,'label':labels[code],'asOfDate':as_of,'allowEntry':code=='FAVORABLE','indexes':evidence}
