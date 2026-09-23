import { expect, test } from '@playwright/test';

async function expectNoHorizontalOverflow(page) {
  const widths = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    document: document.documentElement.scrollWidth,
  }));
  expect(widths.document).toBeLessThanOrEqual(widths.viewport + 1);
}

test('主頁資料、頁籤與計算機在各尺寸可正常操作', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));

  await page.goto('/index.html');
  await expect(page.getByRole('heading', { name: '主升段安全觀察系統' })).toBeVisible();
  await expect(page.locator('#twMeta')).not.toContainText('讀取中');
  await expect(page.locator('#dataHealthStatus')).not.toHaveText('讀取中');
  await expect(page.locator('#marketFilterBox')).toContainText('掃描檔數');
  await expectNoHorizontalOverflow(page);

  await page.locator('#qualityUtilities > summary').click();
  await page.locator('#tabUS').click();
  await expect(page.locator('#tabUS')).toHaveClass(/active/);
  await expect(page.locator('#riskRadar')).toBeHidden();
  await page.locator('#tabTW').click();
  await expect(page.locator('#riskRadar')).toBeVisible();

  await page.getByRole('tab', { name: '資金計算機' }).click();
  await expect(page.locator('#calculatorPage')).toHaveAttribute('aria-hidden', 'false');
  await page.locator('#capital').fill('500000');
  await page.locator('#entryPrice').fill('100');
  await page.locator('#stopLossPrice').fill('95');
  await expect(page.locator('#positionResults')).toContainText('試算股數');

  await page.getByRole('tab', { name: '策略追蹤' }).click();
  await expect(page.locator('#trackingPage')).toHaveAttribute('aria-hidden', 'false');
  await expect.poll(() => page.evaluate(() => Math.abs(trackingPage.getBoundingClientRect().left - pageViewport.getBoundingClientRect().left))).toBeLessThan(1);
  await page.screenshot({ path: 'output/playwright/tracking-' + test.info().project.name + '.png' });
  await page.getByRole('tab', { name: '資金計算機' }).click();
  await expect(page.locator('#capital')).toHaveValue('500000');
  await expectNoHorizontalOverflow(page);
  expect(pageErrors).toEqual([]);
});

test('資料健康檔讀取失敗時顯示降級狀態且頁面不中斷', async ({ page }) => {
  await page.route('**/data/data_health.json*', route => route.abort());
  await page.goto('/index.html');
  await expect(page.locator('#dataHealthStatus')).toHaveText('尚未取得');
  await expect(page.locator('#dataHealthSummary')).toContainText('資料健康報告尚未取得');
  await expect(page.locator('#twMeta')).not.toContainText('讀取中');
});
