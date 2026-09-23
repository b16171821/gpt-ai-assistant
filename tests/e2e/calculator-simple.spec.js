import { expect, test } from '@playwright/test';

test('舊偏好遷移至上限20與成本3，保留本金且不恢復整張模式', async ({ page }) => {
  await page.addInitScript(() => {
    if (!localStorage.getItem('positionCalculatorPreferences.v1')) {
      localStorage.setItem('positionCalculatorPreferences.v1', JSON.stringify({
        remember: true, values: { capital: 300000, riskPercent: 0.5, maxPositionPercent: 10, feeBufferPercent: 0.6, tradeUnit: 1000 },
      }));
    }
  });
  await page.goto('/index.html#calculator');
  await expect(page.locator('#capital')).toHaveValue('300000');
  await expect(page.locator('#riskPercent')).toHaveValue('0.5');
  await expect(page.locator('#maxPositionPercent')).toHaveValue('20');
  await expect(page.locator('#feeBufferPercent')).toHaveValue('3');
  await expect(page.locator('#tradeUnit')).toHaveValue('1');
  await page.locator('#calcAdvanced > summary').click();
  await page.locator('#maxPositionPercent').fill('15');
  await page.locator('#feeBufferPercent').fill('2');
  await page.reload();
  await expect(page.locator('#maxPositionPercent')).toHaveValue('15');
  await expect(page.locator('#feeBufferPercent')).toHaveValue('2');
  await page.locator('#resetCalculator').click();
  await expect(page.locator('#maxPositionPercent')).toHaveValue('20');
  await expect(page.locator('#feeBufferPercent')).toHaveValue('3');
  await expect(page.locator('#tradeUnit')).toHaveValue('1');
});

test('確認試算重新讀取欄位、定位錯誤且不放寬零部位限制', async ({ page }) => {
  await page.goto('/index.html#calculator');
  await page.locator('#confirmPosition').click();
  await expect(page.locator('#capital')).toBeFocused();
  await expect(page.locator('#capitalError')).not.toBeEmpty();
  await expect(page.locator('#positionConfirmStatus')).toContainText('尚未完成試算');
  // Simulate autofill without an input event: confirmation must read DOM values.
  await page.evaluate(() => {
    document.getElementById('capital').value = '500000';
    document.getElementById('entryPrice').value = '100';
    document.getElementById('stopLossPrice').value = '95';
  });
  await page.locator('#confirmPosition').click();
  await expect(page.locator('#positionConfirmStatus')).toContainText('試算完成：1,000股');
  await expect(page.locator('#positionResults')).toContainText('NT$100,000');
  await page.locator('#calcAdvanced > summary').click();
  await page.locator('#maxPositionPercent').fill('0');
  await page.locator('#confirmPosition').click();
  await expect(page.locator('#maxPositionPercent')).toBeFocused();
  await expect(page.locator('#maxPositionPercent')).toHaveValue('0');
  await expect(page.locator('#positionConfirmStatus')).toContainText('確認按鈕不會解除此限制');
  await expect(page.locator('#copyPositionResult')).toBeDisabled();
  await page.locator('#maxPositionPercent').fill('20');
  await page.locator('#capital').fill('1');
  await page.locator('#capital').press('Enter');
  await expect(page.locator('#positionConfirmStatus')).toContainText('試算完成：0 股');
  await expect(page.locator('#zeroReason')).not.toBeEmpty();
});

test('常用金額與精簡結果，固定以一股為單位試算', async ({ page }) => {
  const errors = []; page.on('pageerror', (error) => errors.push(error.message));
  await page.goto('/index.html#calculator');
  await expect(page.locator('#calculatorTab')).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('#calcAdvanced')).not.toHaveAttribute('open', '');
  await page.getByText('50 萬', { exact: true }).click();
  await page.locator('#entryPrice').fill('100');
  await page.locator('#stopLossPrice').fill('95');
  await expect(page.locator('#positionResults')).toContainText('1,000股');
  await expect(page.locator('#positionResults')).toContainText('NT$100,000');
  await expect(page.locator('#positionResults')).toContainText('NT$8,000');
  await expect(page.locator('#maxPositionPercent')).toHaveValue('20');
  await expect(page.locator('#feeBufferPercent')).toHaveValue('3');
  await expect(page.getByRole('combobox', { name: '交易單位' })).toHaveCount(0);
  await expect(page.locator('#calcCostWarning')).toContainText('高於風險容許金額');
  await expect.poll(() => page.evaluate(() => Math.abs(calculatorPage.getBoundingClientRect().left - pageViewport.getBoundingClientRect().left))).toBeLessThan(1);
  await page.evaluate(() => calculatorPage.scrollTo(0, 0));
  await page.screenshot({ path: `output/playwright/calculator-${test.info().project.name}.png` });
  await page.locator('#stopLossPrice').fill('101');
  await expect(page.locator('#stopLossPriceError')).toContainText('必須低於進場價');
  await expect(page.locator('#copyPositionResult')).toBeDisabled();
  await expect(page.locator('#positionResults')).not.toContainText('1,000股');
  await page.locator('#stopLossPrice').fill('95');
  await page.locator('#capital').fill('10000');
  await expect(page.locator('#positionResults')).toContainText('20股');
  await expect(page.locator('#tradeUnit')).toHaveValue('1');
  await expect(page.locator('#tryOddLots')).toHaveCount(0);
  const widths = await page.evaluate(() => [calculatorPage.clientWidth, calculatorPage.scrollWidth, document.documentElement.clientWidth, document.documentElement.scrollWidth]);
  expect(widths[1]).toBeLessThanOrEqual(widths[0] + 1);
  expect(widths[3]).toBeLessThanOrEqual(widths[2] + 1);
  expect(errors).toEqual([]);
});

test('只記住資金偏好、不保留過時價格；可取消記憶與重設', async ({ page }) => {
  await page.goto('/index.html#calculator');
  await page.getByText('30 萬', { exact: true }).click();
  await page.locator('#entryPrice').fill('100');
  await page.locator('#calcAdvanced > summary').click();
  await page.locator('#riskPercent').fill('0.5');
  await page.locator('#feeBufferPercent').fill('2');
  await page.reload();
  await expect(page.locator('#capital')).toHaveValue('300000');
  await expect(page.locator('#riskPercent')).toHaveValue('0.5');
  await expect(page.locator('#tradeUnit')).toHaveValue('1');
  await expect(page.locator('#feeBufferPercent')).toHaveValue('2');
  await expect(page.locator('#entryPrice')).toHaveValue('');
  await page.locator('#rememberCalculator').uncheck();
  await page.reload();
  await expect(page.locator('#capital')).toHaveValue('');
  await expect(page.locator('#rememberCalculator')).not.toBeChecked();
  await page.locator('#rememberCalculator').check();
  await page.getByText('50 萬', { exact: true }).click();
  await page.locator('#resetCalculator').click();
  await page.reload();
  await expect(page.locator('#capital')).toHaveValue('');
});

test('儲存權限被封鎖仍可試算，零部位上限不被快捷操作放寬', async ({ page }) => {
  await page.addInitScript(() => {
    Storage.prototype.getItem = () => { throw new Error('blocked'); };
    Storage.prototype.setItem = () => { throw new Error('blocked'); };
  });
  await page.goto('/index.html#calculator');
  await page.getByText('10 萬', { exact: true }).click();
  await page.locator('#entryPrice').fill('100');
  await page.locator('#stopLossPrice').fill('95');
  await page.locator('#calcAdvanced > summary').click();
  await page.locator('#maxPositionPercent').fill('0');
  await page.getByText('50 萬', { exact: true }).click();
  await expect(page.locator('#maxPositionPercent')).toHaveValue('0');
  await expect(page.locator('#zeroReason')).toContainText('目前不開新倉');
  await expect(page.locator('#copyPositionResult')).toBeDisabled();
});
