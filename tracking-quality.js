(function (root) {
  const VERSION = 'quality-v1';
  const ACTIVE = new Set(['WATCHING', 'WAIT_CONFIRM', 'BREAKOUT_CONFIRMED', 'PULLBACK_CONFIRM', 'IN_TREND', 'NEAR_TARGET']);
  const TERMINAL = new Set(['TARGET_HIT', 'FAILED', 'EXPIRED', 'MANUAL_CLOSED', 'NEED_REVIEW', 'INVALID_AT_CREATION']);
  const quoteTemplates = {
    yahooTW: 'https://tw.stock.yahoo.com/quote/{stockCode}',
    yahooUS: 'https://finance.yahoo.com/quote/{stockCode}/',
    goodinfo: 'https://goodinfo.tw/tw/StockDetail.asp?STOCK_ID={stockCode}',
    tradingview: 'https://www.tradingview.com/symbols/{exchange}-{stockCode}/',
  };
  const finite = (value) => (value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value)) ? Number(value) : null);
  const positive = (value) => (finite(value) > 0 ? Number(value) : null);
  const first = (...values) => values.find((value) => finite(value) !== null) ?? null;
  const pct = (value, base) => (positive(base) && finite(value) !== null ? (value - base) / base * 100 : null);
  function origin(record) {
    return record.trackingOrigin || (record.notes?.legacyLock || record.notes?.legacySnapshots ? 'LEGACY_IMPORT' : 'LIVE_SIGNAL');
  }
  function metrics(record) {
    const o = record.originalStrategy || {}; const
      l = record.latestStatus || {};
    const target = positive(o.originalTarget); const
      stop = positive(o.originalStopLoss);
    const low = positive(o.originalBuyZoneLow); const
      watch = positive(o.originalWatchPrice);
    const close = positive(l.latestClose); const
      entry = low || watch;
    const expectedProfitPercent = pct(target, low || close);
    const stopLossDistancePercent = entry && stop ? (entry - stop) / entry * 100 : null;
    const distanceToNecklinePercent = pct(close, positive(o.originalNeckline));
    const riskReward = finite(o.originalRiskReward);
    const safetyScore = finite(first(l.latestSafetyScore, o.originalSafetyScore));
    const high = Math.max(positive(l.latestHigh) || 0, close || 0);
    const targetProgressPercent = target && high > 0 ? high / target * 100 : null;
    const reasons = [];
    if (expectedProfitPercent === null || expectedProfitPercent < 15) reasons.push(expectedProfitPercent === null ? '缺少獲利空間資料' : '獲利空間低於 15%，移入備查，不列主追蹤。');
    if (safetyScore === null || safetyScore < 85) reasons.push('安全分未達 85');
    if (riskReward === null || riskReward < 2) reasons.push('風報比未達 2');
    if (!(stopLossDistancePercent > 0 && stopLossDistancePercent <= 5.5)) reasons.push('停損距離不在 0%～5.5%');
    if (distanceToNecklinePercent === null || Math.abs(distanceToNecklinePercent) > 5) reasons.push('距頸線超過 5% 或資料不足');
    if (!target || !stop || !entry || target <= entry || stop >= entry) reasons.push('原始價位不完整或無效');
    if (record.trackingStatus === 'INVALID_AT_CREATION') reasons.push('建立時已失效');
    if (/完全無效|型態失敗|資料不足/.test(l.adjustedSignal || '')) reasons.push('訊號無效或資料不足');
    if (close && stop && close <= stop) reasons.push('收盤已觸及原始停損');
    const highQualityTracking = reasons.length === 0;
    const highPriority = highQualityTracking && safetyScore >= 90 && expectedProfitPercent >= 20 && riskReward >= 3;
    const lowQuality = expectedProfitPercent === null || expectedProfitPercent < 15 || riskReward === null || riskReward < 2 || !target || !stop || !entry;
    return {
      expectedProfitPercent, stopLossDistancePercent, distanceToNecklinePercent, safetyScore, riskReward, targetProgressPercent, highQualityTracking, highPriority, lowQuality, reasons, entry,
    };
  }
  function strongEvidence(record, m, context) {
    const l = record.latestStatus || {}; const
      o = record.originalStrategy || {};
    const date = l.latestDataDate || record.lastUpdateDate;
    return Boolean(context?.status === 'COMPLETE' && context.asOfDate === date
      && finite(context.stockReturn5Pct) !== null && finite(context.marketReturn5Pct) !== null
      && context.stockReturn5Pct > context.marketReturn5Pct
      && positive(l.latestMA20) && l.latestClose > l.latestMA20
      && positive(o.originalNeckline) && l.latestClose >= o.originalNeckline
      && m.highQualityTracking && m.safetyScore >= 90 && m.riskReward >= 3
      && m.expectedProfitPercent >= 15 && context.liquidityOk === true);
  }
  function marketPolicy(record, m = metrics(record), context = null, regimeOverride) {
    const l = record.latestStatus || {}; const
      o = record.originalStrategy || {};
    const regime = regimeOverride || l.latestMarketRegime || o.originalMarketRegime || 'NO_DATA';
    const signal = l.latestSignal || o.originalSignal || '';
    const pullback = /回踩/.test(signal) || record.trackingStatus === 'PULLBACK_CONFIRM';
    const strong = strongEvidence(record, m, context);
    const policies = {
      ATTACK: {
        label: '進攻盤，依原策略確認', signal: m.highQualityTracking ? (pullback ? '回踩買點' : /突破/.test(signal) ? '突破買點' : '等待確認') : '等待確認', allowEntry: m.highQualityTracking, maxPositionPercent: 20, cashAction: '依原策略等待確認，超過觀察區不追。',
      },
      WATCH: {
        label: '觀察盤，買點降級', signal: pullback && m.highQualityTracking ? '小部位試單' : '等待確認', allowEntry: pullback && m.highQualityTracking, maxPositionPercent: 10, cashAction: pullback && m.highQualityTracking ? '回踩確認後小部位試單，試算部位上限 10%。' : '突破尚未確認，等待收盤站穩。',
      },
      DEFENSE: {
        label: '防守盤，買點降級觀察', signal: strong ? '防守盤強勢觀察' : '等待大盤止跌', allowEntry: false, maxPositionPercent: 0, cashAction: '不追價，等待大盤止跌或轉觀察盤。',
      },
      STOP: {
        label: '停止進場，持股風控', signal: strong ? '抗跌追蹤' : '持股風控', allowEntry: false, maxPositionPercent: 0, cashAction: '不開新倉，等大盤止跌。',
      },
      NO_DATA: {
        label: '大盤資料不足', signal: '等待確認', allowEntry: false, maxPositionPercent: 0, cashAction: '等待完整收盤資料。',
      },
    };
    const policy = policies[regime] || policies.NO_DATA;
    return {
      ...policy, regime, strongObservation: strong && ['DEFENSE', 'STOP'].includes(regime), holderAction: '守原始停損，接近目標分批停利。',
    };
  }
  function view(record, context, regime) {
    const m = metrics(record); const
      policy = marketPolicy(record, m, context, regime);
    const terminal = TERMINAL.has(record.trackingStatus) || record.manualRemoved === true;
    const legacy = origin(record) === 'LEGACY_IMPORT';
    const highQualityTracking = m.highQualityTracking && !legacy;
    const tier = terminal ? 'ENDED' : highQualityTracking ? 'MAIN' : m.lowQuality || legacy ? 'HIDDEN' : 'RESERVE';
    const endedLabels = {
      TARGET_HIT: '目標達成', FAILED: '停損結束', EXPIRED: '觀察過期', MANUAL_CLOSED: '手動結束', NEED_REVIEW: '同日觸發待確認', INVALID_AT_CREATION: '無效建立',
    };
    let label = '等待確認'; let
      reason = '突破尚未確認，等待收盤站穩。';
    if (terminal) { label = endedLabels[record.trackingStatus] || '手動結束'; reason = record.endReason || label; } else if (m.targetProgressPercent >= 95) { label = '接近目標'; reason = '接近目標，優先分批停利。'; } else if (m.targetProgressPercent >= 90) { label = '停利警戒'; reason = '已到原始目標九成，注意分批停利。'; } else if (tier !== 'MAIN') { label = '備查'; reason = m.reasons[0] || '歷史回補，保留備查。'; } else if (policy.strongObservation) { label = '強勢觀察'; reason = policy.signal; } else if (['STOP', 'DEFENSE'].includes(policy.regime)) { label = '持股風控'; reason = policy.label; } else if (m.highPriority) { label = '高優先'; reason = policy.signal; } else { reason = policy.signal; }
    if (!terminal && m.expectedProfitPercent !== null && m.expectedProfitPercent < 15) {
      reason = '獲利空間低於 15%，移入備查，不列主追蹤。';
    }
    return {
      ...m, highQualityTracking, highPriority: highQualityTracking && m.highPriority, policy, tier, terminal, label, reason, nearTarget: m.targetProgressPercent >= 90,
    };
  }
  function freezeAdmission(record, basis = 'RECORDED_AT_CREATION') {
    const firstDate = record.firstSignalDate;
    const firstRow = (record.statusHistory || []).find((item) => item.date === firstDate);
    const initial = {
      ...record,
      trackingStatus: firstRow?.status || record.trackingStatus,
      latestStatus: firstRow?.latestStatus || record.latestStatus,
    };
    const m = metrics(initial); const
      regime = record.originalStrategy?.originalMarketRegime;
    const effective = ['BREAKOUT_CONFIRMED', 'PULLBACK_CONFIRM', 'IN_TREND', 'NEAR_TARGET'].includes(initial.trackingStatus)
      && !/觀察|等待|降級/.test(record.originalStrategy?.originalSignal || '');
    const entryAllowed = marketPolicy(initial, m, null, regime).allowEntry;
    const eligible = m.highQualityTracking && origin(record) === 'LIVE_SIGNAL'
      && ['ATTACK', 'WATCH'].includes(regime) && effective
      && entryAllowed
      && initial.trackingStatus !== 'INVALID_AT_CREATION';
    return {
      version: VERSION,
      basis,
      firstSignalDate: firstDate,
      eligible,
      originalMarketRegime: regime,
      effectiveAtCreation: effective,
      metrics: m,
      exclusionReasons: [...m.reasons,
        ...(!['ATTACK', 'WATCH'].includes(regime) ? ['建立時僅供觀察'] : []),
        ...(!effective ? ['建立時尚未確認有效策略'] : []),
        ...(!entryAllowed ? ['建立時不開放進場'] : []),
        ...(origin(record) !== 'LIVE_SIGNAL' ? ['歷史回補'] : [])],
    };
  }
  function statistics(records, admissions = {}) {
    const cohort = records.filter((r) => {
      const a = admissions[r.trackingId];
      return a?.version === VERSION && a.basis === 'RECORDED_AT_CREATION' && a.eligible
        && a.firstSignalDate === r.firstSignalDate && origin(r) === 'LIVE_SIGNAL'
        && r.trackingStatus !== 'INVALID_AT_CREATION';
    });
    const count = (status) => cohort.filter((r) => r.trackingStatus === status).length;
    const hit = count('TARGET_HIT'); const failed = count('FAILED'); const
      expired = count('EXPIRED');
    const active = cohort.filter((r) => ACTIVE.has(r.trackingStatus)).length;
    const resolved = hit + failed + expired;
    return {
      active,
      hit,
      failed,
      expired,
      resolved,
      total: cohort.length,
      review: count('NEED_REVIEW'),
      manual: count('MANUAL_CLOSED'),
      targetRate: resolved >= 10 ? hit / resolved * 100 : null,
    };
  }
  function matches(record, v, query, filter) {
    const q = String(query || '').trim().toLocaleLowerCase();
    if (q && !String(`${record.stockCode} ${record.stockName}`).toLocaleLowerCase().includes(q)) return false;
    return ({
      ALL: true,
      PRIORITY: v.highPriority,
      NEAR: v.nearTarget,
      WAIT: v.label === '等待確認',
      STRONG: v.policy.strongObservation,
      TRIAL: v.policy.signal === '小部位試單',
      PROFIT: v.expectedProfitPercent >= 15,
      SAFE: v.safetyScore >= 90,
      RR: v.riskReward >= 3,
    })[filter || 'ALL'] === true;
  }
  function compare(a, b, sort = 'default') {
    const av = a.view; const
      bv = b.view;
    const desc = (key) => (finite(bv[key]) ?? -Infinity) - (finite(av[key]) ?? -Infinity);
    const code = () => String(a.record.stockCode).localeCompare(String(b.record.stockCode), 'zh-Hant', { numeric: true });
    if (sort === 'code') return code();
    if (sort === 'date') return String(b.record.firstSignalDate).localeCompare(String(a.record.firstSignalDate)) || code();
    const key = { profit: 'expectedProfitPercent', safety: 'safetyScore', rr: 'riskReward' }[sort];
    if (key) return desc(key) || code();
    return (sort === 'near' ? 0 : Number(bv.highPriority) - Number(av.highPriority))
      || Number(bv.nearTarget) - Number(av.nearTarget) || desc('expectedProfitPercent')
      || desc('safetyScore') || desc('riskReward') || code();
  }
  function quoteUrl(record, provider = 'yahooTW') {
    const code = encodeURIComponent(record.stockCode || record.code || record.ticker || '');
    const market = record.market || record.marketName;
    const { exchange } = record;
    const selected = market === '美股' ? 'yahooUS' : provider === 'tradingview' && !['TWSE', 'TPEX'].includes(exchange) ? 'yahooTW' : provider;
    return (quoteTemplates[selected] || quoteTemplates.yahooTW).replace('{stockCode}', code).replace('{exchange}', encodeURIComponent(exchange || ''));
  }
  function fromStock(row) {
    const p = row.plan || {};
    return {
      trackingId: `current:${row.code || row.ticker}`,
      stockCode: row.code || row.ticker,
      stockName: row.name,
      market: row.marketName,
      trackingOrigin: 'LIVE_SIGNAL',
      trackingStatus: /回踩/.test(row.stage || '') ? 'PULLBACK_CONFIRM' : 'WATCHING',
      firstSignalDate: row.strategyAsOfDate || row.date,
      originalStrategy: {
        originalBuyZoneLow: p.lo || row.chaseRangeLow || row.buyLow,
        originalBuyZoneHigh: p.hi || row.chaseRangeHigh || row.buyHigh,
        originalWatchPrice: p.entry || row.observationEntry,
        originalTarget: row.target,
        originalStopLoss: row.stopLoss,
        originalNeckline: row.neckline,
        originalSafetyScore: row.safeScore,
        originalRiskReward: row.riskRewardRatio,
        originalSignal: row.buyPointType || row.originalSignal,
        originalMarketRegime: row.marketRegime,
      },
      latestStatus: {
        latestClose: row.close,
        latestHigh: row.high,
        latestMA20: row.ma20,
        latestSafetyScore: row.safeScore,
        latestSignal: row.buyPointType || row.originalSignal,
        latestDataDate: row.strategyAsOfDate || row.date,
        latestMarketRegime: row.marketRegime,
      },
    };
  }
  root.TrackingQuality = {
    VERSION, ACTIVE, TERMINAL, metrics, origin, view, marketPolicy, freezeAdmission, statistics, matches, compare, quoteUrl, quoteTemplates, fromStock,
  };
}(typeof window === 'undefined' ? globalThis : window));
