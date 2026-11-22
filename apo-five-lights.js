/* Shared numeric rules for 阿波五燈交易系統 1.0.
   Grading stays in scripts/apo_five_lights.py. This file only repeats the
   position-size formula and refuses to invent a grade for old payloads. */
(function initializeApoFiveLights(root) {
  const FEE_RATE = 0.001425;
  const FEE_DISCOUNT = 1;
  const SELL_TAX_RATE = 0.003;
  const RISK_FRACTION = 0.01;
  const LIQUIDITY_PARTICIPATION = 0.05;

  function sf(value) {
    const number = typeof value === 'number' ? value : Number(value);
    return Number.isFinite(number) ? number : 0;
  }

  function roundTripRiskPerShare(entry, stop, feeRate, feeDiscount, sellTaxRate) {
    const rate = feeRate == null ? FEE_RATE : feeRate;
    const discount = feeDiscount == null ? FEE_DISCOUNT : feeDiscount;
    const tax = sellTaxRate == null ? SELL_TAX_RATE : sellTaxRate;
    if (!(entry > 0) || !(stop > 0) || stop >= entry) return null;
    return (entry - stop) + (entry * rate * discount) + (stop * rate * discount) + (stop * tax);
  }

  function positionSize(input) {
    const values = input || {};
    const capital = sf(values.capital);
    const entry = sf(values.entry);
    const stop = sf(values.stop);
    const lot = Math.max(1, Math.floor(sf(values.lotSize) || 1));
    const feeRate = values.feeRate == null ? FEE_RATE : sf(values.feeRate);
    const feeDiscount = values.feeDiscount == null ? FEE_DISCOUNT : sf(values.feeDiscount);
    const sellTaxRate = values.sellTaxRate == null ? SELL_TAX_RATE : sf(values.sellTaxRate);
    const riskFraction = values.riskFraction == null ? RISK_FRACTION : sf(values.riskFraction);
    const maxPositionPct = values.maxPositionPct == null ? 1 : sf(values.maxPositionPct);
    const participation = values.participation == null ? LIQUIDITY_PARTICIPATION : sf(values.participation);
    const result = {
      shares: 0,
      sharesBeforeLot: 0,
      riskPerShare: null,
      riskBudget: Math.max(0, capital * riskFraction),
      liquidityCapped: false,
      reason: '',
    };
    if (capital <= 0 || entry <= 0 || stop <= 0) {
      result.reason = '資金或價格無效';
      return result;
    }
    if (stop >= entry) {
      result.reason = '停損必須低於進場價';
      return result;
    }
    const perShare = roundTripRiskPerShare(entry, stop, feeRate, feeDiscount, sellTaxRate);
    result.riskPerShare = perShare;
    if (perShare == null || perShare <= 0 || result.riskBudget <= 0) {
      result.reason = '風險額度不足';
      return result;
    }
    let raw = result.riskBudget / perShare;
    const buyCash = entry * (1 + feeRate * feeDiscount);
    if (maxPositionPct > 0 && buyCash > 0) {
      raw = Math.min(raw, (capital * maxPositionPct) / buyCash);
    }
    const turnover = sf(values.avgTurnover);
    if (turnover > 0 && entry > 0) {
      const liquidShares = (turnover * participation) / entry;
      if (raw > liquidShares) {
        result.liquidityCapped = true;
        raw = liquidShares;
      }
    }
    result.sharesBeforeLot = raw > 0 ? Math.floor(raw) : 0;
    result.shares = raw > 0 ? Math.floor(raw / lot) * lot : 0;
    if (result.shares <= 0) result.reason = '依張數或流動性向下取整後為 0';
    return result;
  }

  function present(row) {
    const source = row || {};
    const known = source.strategySystem === 'apo-five-lights-1.0';
    const rating = known ? (source.rating || source.rawRating || '未核實') : '未核實';
    return {
      rating,
      grade: rating,
      unverified: !known || rating === '未核實',
      backtestStatus: '尚未回測',
    };
  }

  const api = {
    FEE_RATE,
    FEE_DISCOUNT,
    SELL_TAX_RATE,
    RISK_FRACTION,
    roundTripRiskPerShare,
    positionSize,
    present,
    SYSTEM: 'apo-five-lights-1.0',
  };
  root.ApoFiveLights = api;
  if (typeof module === 'object' && module.exports) module.exports = api;
}(typeof globalThis !== 'undefined' ? globalThis : this));
