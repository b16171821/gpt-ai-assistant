import { expect, test } from '@playwright/test';

const date = '2026-09-22';
function record(index, target = 125) {
  return {
    trackingId: `fixture-${index}`,
    market: '台股',
    stockCode: String(9000 + index),
    stockName: `測試標的${index}`,
    firstSignalDate: date,
    lastUpdateDate: date,
    trackingCreatedDate: date,
    trackingOrigin: 'LIVE_SIGNAL',
    trackingStatus: 'PULLBACK_CONFIRM',
    originalStrategy: {
      originalBuyZoneLow: 100,
      originalBuyZoneHigh: 103,
      originalWatchPrice: 102,
      originalStopLoss: 95,
      originalTarget: target,
      originalNeckline: 100,
      originalSafetyScore: 95,
      originalRiskReward: 5,
      originalMarketRegime: 'WATCH',
      originalSignal: '回踩買點',
    },
    latestStatus: {
      latestClose: 101,
      latestHigh: 102,
      latestMA20: 99,
      latestSafetyScore: 95,
      latestMarketRegime: 'WATCH',
      latestSignal: '回踩買點',
      latestDataDate: date,
    },
    progress: {},
    notes: { auto: [] },
  };
}
async function setup(page, marketRegime = 'WATCH') {
  const records = Array.from({ length: 20 }, (_, i) => record(i, 125 + i));
  records[19].originalStrategy.originalSignal = '突破買點';
  records[19].originalStrategy.originalStage = '突破第一根';
  records.push(record(30, 110));
  const stale = record(31); stale.latestStatus.latestDataDate = '2026-09-21'; records.push(stale);
  const ended = record(32); ended.trackingStatus = 'FAILED'; ended.endedAt = date; records.push(ended);
  const fulfill = (data) => (route) => route.fulfill({ json: data });
  await page.route('**/data/latest.json*', fulfill({ meta: { strategyAsOfDate: date, freshnessStatus: 'COMPLETE', marketRegime }, stocks: [] }));
  await page.route('**/data/strategy_tracking.json*', fulfill({ meta: { updatedAt: `${date}T18:00:00+08:00` }, records }));
  await page.route('**/data/disposition_risk.json*', fulfill({ meta: { updatedAt: date, counts: {} }, normalCodes: records.map((r) => r.stockCode), records: {} }));
  await page.route('**/data/tracking_quality.json*', fulfill({ records: {} }));
  await page.route('**/data/tracking_context.json*', fulfill({ records: {} }));
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'clipboard', { value: { writeText: async (text) => { window.copiedStrategy = text; } } });
  });
  await page.goto('/index.html#tracking');
  await expect(page.locator('#quality-main-count')).toHaveText('20 / 20 檔');
  return records;
}

test('追蹤每次15檔、搜尋排序、隱藏與備查分層，詳細預設收合', async ({ page }) => {
  const errors = []; page.on('pageerror', (error) => errors.push(error.message));
  await setup(page);
  const cards = page.locator('#quality-main-list .qualityCard');
  await expect(cards).toHaveCount(15);
  await expect(cards.first()).toContainText('9019');
  await expect(cards.first().locator('.qualityDetails')).not.toHaveAttribute('open', '');
  await expect(cards.first().locator('.qualityBuyZone')).toBeVisible();
  await expect(cards.first().locator('.qualityBuyZone')).toHaveText('原始買入觀察區100～103 元');
  for (const [label, value] of [['原始頸線', '100 元'], ['風報比', '5'], ['安全分', '95']]) {
    const metric = cards.first().locator('.qualityMetrics > div').filter({ has: page.getByText(label, { exact: true }) });
    await expect(metric).toBeVisible();
    await expect(metric.locator('b')).toHaveText(value);
  }
  await page.locator('#quality-main-more').click();
  await expect(cards).toHaveCount(20);
  await page.locator('#trackingSort').selectOption('code');
  await expect(cards.first()).toContainText('9000');
  await page.locator('#trackingSearch').fill('9012');
  await expect(cards).toHaveCount(1);
  await expect(cards.first()).toContainText('測試標的12');
  await page.locator('#trackingSearch').fill('');
  await expect(cards).toHaveCount(15);
  await page.locator('[data-quality-filter="TRIAL"]').click();
  await expect(cards).toHaveCount(15);
  await page.locator('[data-quality-filter="ALL"]').click();
  await expect(page.locator('#quality-hidden')).toBeHidden();
  await page.locator('[data-quality-toggle="showAll"]').check();
  await page.locator('#quality-hidden > summary').click();
  await expect(page.locator('#quality-hidden-list')).toContainText('獲利空間低於 15%');
  await page.locator('#quality-reserve > summary').click();
  await expect(page.locator('#quality-reserve-list')).toContainText('收盤資料待更新');
  const widths = await page.evaluate(() => ({
    width: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth,
    panel: trackingPage.clientWidth,
    content: trackingPage.scrollWidth,
  }));
  expect(widths.scroll).toBeLessThanOrEqual(widths.width + 1);
  expect(widths.content).toBeLessThanOrEqual(widths.panel + 1);
  expect(errors).toEqual([]);
});

test('STOP帶入與重新帶入不覆蓋20%上限和3%成本', async ({ page }) => {
  await setup(page, 'STOP');
  await page.locator('[data-tracking-id="fixture-19"]').getByRole('button', { name: '帶入部位計算' }).click();
  await expect(page.locator('#maxPositionPercent')).toHaveValue('20');
  await expect(page.locator('#feeBufferPercent')).toHaveValue('3');
  await expect(page.locator('#calculatorStatus')).toContainText('策略部位上限 0%');
  await page.locator('#reloadAnalysisValues').click();
  await expect(page.locator('#maxPositionPercent')).toHaveValue('20');
  await expect(page.locator('#feeBufferPercent')).toHaveValue('3');
  await page.locator('#capital').fill('500000');
  await page.locator('#confirmPosition').click();
  await expect(page.locator('#positionConfirmStatus')).toContainText('試算完成');
  await expect(page.locator('#copyPositionResult')).toBeEnabled();
});

test('訊號說明區分建立時與最新訊號，終局與缺漏不假造進場許可', async ({ page }) => {
  const records = await setup(page);
  const card = page.locator('[data-tracking-id="fixture-19"]');
  await expect(card.locator('.qualityStageRow')).toBeVisible();
  await expect(card.locator('.qualityStageRow')).toContainText('目前階段回踩確認');
  await expect(card.locator('.qualityStageRow')).toContainText('建立時型態突破第一根');
  await card.locator('.qualityDetails > summary').click();
  const section = card.getByRole('region', { name: '訊號與階段說明' });
  await expect(section.locator('.qualityCompactFields > div').filter({ hasText: '原始訊號（建立時，固定）' })).toContainText('突破買點');
  await expect(section.locator('.qualityCompactFields > div').filter({ hasText: '目前技術訊號（最新資料）' })).toContainText('回踩買點');
  await expect(section.locator('.qualityCompactFields > div').filter({ hasText: '分級後訊號（目前限制）' })).toContainText('小部位試單');
  await expect(section.locator('.qualitySignalHelp')).not.toHaveAttribute('open', '');
  await expect(card.locator('.qualityNotes').filter({ hasText: '原始鎖定資料與計算口徑' })).not.toHaveAttribute('open', '');
  expect(await card.locator('.qualityDetails > .trackingDetail').evaluate((el) => el.getBoundingClientRect().height)).toBeLessThan(800);
  await section.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `output/playwright/tracking-compact-${test.info().project.name}.png` });
  await section.locator('.qualitySignalHelp > summary').click();
  await expect(section).toContainText('原始型態階段（建立時）');
  await expect(section).toContainText('目前追蹤階段（生命週期）');
  await expect(section).toContainText('不代表大盤已允許新進場');
  const results = await page.evaluate(() => {
    const source = trackingData.records[19];
    const ended = { ...source, trackingStatus: 'TARGET_HIT' };
    const missing = { ...source, originalStrategy: {}, latestStatus: {} };
    return {
      original: source.originalStrategy,
      ended: trackingSignalSection(ended, qualityTrackingView(ended)),
      missing: trackingSignalSection(missing, qualityTrackingView(missing)),
      unknown: trackingSignalMeaning('<script>unknown</script>'),
    };
  });
  expect(results.original).toEqual(records[19].originalStrategy);
  expect(results.ended).toContain('原策略已結束');
  expect(results.ended).toContain('後續回跌不改寫達標結果');
  expect(results.missing).toContain('資料未提供，不推測');
  expect(results.unknown).toContain('不能單憑名稱推定已確認進場');
  const widths = await section.evaluate((el) => [el.clientWidth, el.scrollWidth]);
  expect(widths[1]).toBeLessThanOrEqual(widths[0] + 1);
  await section.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `output/playwright/signal-explanation-${test.info().project.name}.png` });
});

test('行情、複製與試算沿用原始策略，背景刷新不蓋手動價格', async ({ page }) => {
  const records = await setup(page);
  const card = page.locator('[data-tracking-id="fixture-19"]');
  await expect(card.getByRole('link', { name: /查看股價/ })).toHaveAttribute('href', 'https://tw.stock.yahoo.com/quote/9019');
  await expect(card.getByRole('link', { name: /查看股價/ })).toHaveAttribute('rel', 'noopener noreferrer');
  await card.locator('.qualityDetails > summary').click();
  await expect(card.locator('.trackingSubsection').filter({ has: page.getByText('目前判讀', { exact: true }) })).toContainText('距第一目標尚有：42.57%');
  await expect(card).not.toContainText('股價占第一目標');
  await card.getByRole('button', { name: '複製完整策略' }).click();
  await expect.poll(() => page.evaluate(() => window.copiedStrategy)).toContain('小部位試單');
  expect(await page.evaluate(() => window.copiedStrategy)).toContain('144');
  await card.getByRole('button', { name: '帶入部位計算' }).click();
  await expect(page.locator('#calculatorTab')).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('#entryPrice')).toHaveValue('101.5');
  await expect(page.locator('#stopLossPrice')).toHaveValue('95');
  await expect(page.locator('#maxPositionPercent')).toHaveValue('20');
  await expect(page.locator('#calculatorStatus')).toContainText('策略部位上限 10%');
  await page.locator('#calcAdvanced > summary').click();
  await page.locator('#maxPositionPercent').fill('15');
  await page.locator('#reloadAnalysisValues').click();
  await expect(page.locator('#maxPositionPercent')).toHaveValue('15');
  await page.locator('#entryPrice').fill('99.5');
  await page.evaluate(() => loadAll());
  await page.getByRole('tab', { name: '策略追蹤' }).click();
  await page.getByRole('tab', { name: '資金計算機' }).click();
  await expect(page.locator('#entryPrice')).toHaveValue('99.5');
  const actual = await page.evaluate(() => trackingData.records[19]);
  expect(actual.originalStrategy).toEqual(records[19].originalStrategy);
  expect(actual.firstSignalDate).toBe(records[19].firstSignalDate);
});

test('目標距離以收盤計算，已達標後回跌不改回未達標', async ({ page }) => {
  await setup(page);
  const results = await page.evaluate(() => {
    const base = { trackingStatus: 'WATCHING', originalStrategy: { originalTarget: 100 }, latestStatus: { latestClose: 90, latestHigh: 95 } };
    const before = JSON.stringify(base);
    return {
      remaining: qualityTargetDistanceText(base),
      unchanged: before === JSON.stringify(base),
      hit: qualityTargetDistanceText({ ...base, trackingStatus: 'TARGET_HIT', latestStatus: { latestClose: 70 } }),
      review: qualityTargetDistanceText({ ...base, trackingStatus: 'NEED_REVIEW' }),
      failed: qualityTargetDistanceText({ ...base, trackingStatus: 'FAILED', targetHitDate: '2026-09-23' }),
      missing: qualityTargetDistanceText({ ...base, latestStatus: { latestClose: 0 } }),
      pending: qualityTargetDistanceText({ ...base, latestStatus: { latestClose: 101 } }),
    };
  });
  expect(results).toEqual({ remaining: '距第一目標尚有：11.11%', unchanged: true,
    hit: '已達第一目標', review: '同日觸發，需人工確認', failed: '策略已結束',
    missing: '距第一目標：資料不足', pending: '收盤已達第一目標，待確認追蹤結果' });
});

test('新資料檔缺漏不使網站壞掉，不捏造強勢證據或達成率', async ({ page }) => {
  await setup(page);
  await page.route('**/data/tracking_quality.json*', (route) => route.abort());
  await page.route('**/data/tracking_context.json*', (route) => route.abort());
  await page.evaluate(() => loadAll());
  await expect(page.locator('#qualityStats')).toContainText('樣本不足');
  await expect(page.locator('#quality-main-list .qualityCard')).toHaveCount(15);
  await page.locator('[data-quality-filter="STRONG"]').click();
  await expect(page.locator('#quality-main-list')).toContainText('沒有符合');
});
