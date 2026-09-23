import { test } from 'node:test';
import assert from 'node:assert/strict';
import '../tracking-quality.js';

const q = globalThis.TrackingQuality;
const clone = (object) => structuredClone(object);
const base = () => ({
  trackingId: '台股:2330:2026-09-22',
  market: '台股',
  stockCode: '2330',
  stockName: '測試股',
  firstSignalDate: '2026-09-22',
  lastUpdateDate: '2026-09-22',
  trackingOrigin: 'LIVE_SIGNAL',
  trackingStatus: 'BREAKOUT_CONFIRMED',
  originalStrategy: {
    originalBuyZoneLow: 100,
    originalBuyZoneHigh: 103,
    originalWatchPrice: 102,
    originalStopLoss: 95,
    originalTarget: 125,
    originalNeckline: 100,
    originalSafetyScore: 95,
    originalRiskReward: 5,
    originalMarketRegime: 'ATTACK',
    originalSignal: '突破買點',
  },
  latestStatus: {
    latestClose: 101,
    latestHigh: 102,
    latestMA20: 99,
    latestSafetyScore: 95,
    latestMarketRegime: 'ATTACK',
    latestSignal: '突破買點',
    latestDataDate: '2026-09-22',
  },
});

test('15% boundary, exact entry base and missing-price fallback', () => {
  const r = base();
  r.originalStrategy.originalTarget = 115;
  assert.equal(q.metrics(r).expectedProfitPercent, 15);
  assert.equal(q.view(r).tier, 'MAIN');
  r.originalStrategy.originalTarget = 114.99;
  assert.equal(q.view(r).tier, 'HIDDEN');
  delete r.originalStrategy.originalBuyZoneLow;
  assert.equal(q.metrics(r).expectedProfitPercent, (114.99 - 101) / 101 * 100);
});
test('Missing, zero and nonfinite fields never become high quality', () => {
  for (const field of ['originalTarget', 'originalStopLoss', 'originalRiskReward']) {
    for (const value of [null, 0, NaN, Infinity]) {
      const r = base(); r.originalStrategy[field] = value;
      assert.equal(q.view(r).highQualityTracking, false);
    }
  }
  const r = base(); r.latestStatus.latestSafetyScore = 0;
  assert.equal(q.metrics(r).safetyScore, 0);
});
test('Quality gates use absolute neckline distance and valid positive stop distance', () => {
  const r = base(); r.latestStatus.latestClose = 90;
  assert.equal(q.view(r).highQualityTracking, false);
  r.latestStatus.latestClose = 101; r.originalStrategy.originalStopLoss = 101;
  assert.equal(q.view(r).highQualityTracking, false);
});
test('Original strategy and tracking input remain byte-for-byte unchanged', () => {
  const r = base(); const
    before = JSON.stringify(r);
  q.metrics(r); q.view(r); q.freezeAdmission(r);
  assert.equal(JSON.stringify(r), before);
});
test('WATCH downgrades breakout and permits only qualifying pullback at 10%', () => {
  const r = base();
  let p = q.marketPolicy(r, q.metrics(r), null, 'WATCH');
  assert.equal(p.signal, '等待確認'); assert.equal(p.allowEntry, false);
  r.latestStatus.latestSignal = '回踩買點';
  p = q.marketPolicy(r, q.metrics(r), null, 'WATCH');
  assert.equal(p.signal, '小部位試單'); assert.equal(p.maxPositionPercent, 10);
});
test('DEFENSE strong observation needs aligned, fresh evidence, never entry', () => {
  const r = base();
  const evidence = {
    status: 'COMPLETE', asOfDate: '2026-09-22', stockReturn5Pct: 4, marketReturn5Pct: 1, liquidityOk: true,
  };
  let p = q.marketPolicy(r, q.metrics(r), evidence, 'DEFENSE');
  assert.equal(p.signal, '防守盤強勢觀察'); assert.equal(p.allowEntry, false);
  assert.equal(p.maxPositionPercent, 0);
  for (const patch of [{ asOfDate: '2026-09-21' }, { stockReturn5Pct: null }, { liquidityOk: false }, { marketReturn5Pct: 6 }]) {
    p = q.marketPolicy(r, q.metrics(r), { ...evidence, ...patch }, 'DEFENSE');
    assert.equal(p.strongObservation, false);
  }
});
test('STOP keeps high-quality observations but never enables entry', () => {
  const r = base(); r.latestStatus.latestMarketRegime = 'STOP';
  const v = q.view(r);
  assert.equal(v.tier, 'MAIN'); assert.equal(v.label, '持股風控');
  assert.equal(v.policy.allowEntry, false); assert.equal(v.policy.maxPositionPercent, 0);
});
test('90/95% high-price target progress and terminal precedence', () => {
  const r = base();
  r.latestStatus.latestHigh = 112.5; assert.equal(q.view(r).label, '停利警戒');
  r.latestStatus.latestHigh = 118.75; assert.equal(q.view(r).label, '接近目標');
  r.trackingStatus = 'FAILED'; assert.equal(q.view(r).label, '停損結束');
  r.trackingStatus = 'TARGET_HIT'; r.latestStatus.latestClose = 80;
  assert.equal(q.view(r).label, '目標達成');
});
test('Fixed admission retains losing members even when current quality deteriorates', () => {
  const original = base(); const
    admission = q.freezeAdmission(original);
  assert.equal(admission.eligible, true);
  const loser = clone(original); loser.trackingStatus = 'FAILED';
  loser.latestStatus.latestSafetyScore = 20; loser.latestStatus.latestClose = 90;
  const stats = q.statistics([loser], { [loser.trackingId]: admission });
  assert.equal(stats.failed, 1); assert.equal(stats.resolved, 1);
  assert.equal(stats.targetRate, null);
});
test('Historical, STOP-at-creation and unconfirmed observations excluded from formal cohort', () => {
  const r = base();
  assert.equal(q.statistics([r], { [r.trackingId]: q.freezeAdmission(r, 'HISTORICAL_REVIEW') }).total, 0);
  r.originalStrategy.originalMarketRegime = 'STOP';
  assert.equal(q.freezeAdmission(r).eligible, false);
  r.originalStrategy.originalMarketRegime = 'ATTACK'; r.trackingStatus = 'WATCHING';
  assert.equal(q.freezeAdmission(r).eligible, false);
});
test('Rate has transparent denominator including admitted expired records', () => {
  const records = []; const
    admissions = {};
  for (let i = 0; i < 10; i++) {
    const r = base(); r.trackingId += `:${i}`;
    admissions[r.trackingId] = q.freezeAdmission(r);
    r.trackingStatus = i < 5 ? 'TARGET_HIT' : i < 9 ? 'FAILED' : 'EXPIRED';
    records.push(r);
  }
  assert.deepEqual(q.statistics(records, admissions), {
    active: 0, hit: 5, failed: 4, expired: 1, resolved: 10, total: 10, review: 0, manual: 0, targetRate: 50,
  });
});
test('Search, filters, sorting and quote URLs are deterministic', () => {
  const a = base(); const
    b = base(); b.stockCode = '1101'; b.stockName = '台泥';
  b.originalStrategy.originalTarget = 120;
  assert.equal(q.matches(a, q.view(a), '2330', 'PRIORITY'), true);
  assert.equal(q.matches(a, q.view(a), '不存在', 'ALL'), false);
  assert.equal(q.matches(b, q.view(b), '台泥', 'PROFIT'), true);
  assert.equal([{ record: b, view: q.view(b) }, { record: a, view: q.view(a) }].sort(q.compare)[0].record.stockCode, '2330');
  assert.equal(q.quoteUrl(a, 'tradingview'), 'https://tw.stock.yahoo.com/quote/2330');
  assert.equal(q.quoteUrl({ market: '美股', stockCode: 'BRK/B' }), 'https://finance.yahoo.com/quote/BRK%2FB/');
});
