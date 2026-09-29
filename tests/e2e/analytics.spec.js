import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';

const script = readFileSync(new URL('../../site-analytics.js', import.meta.url), 'utf8');
const css = readFileSync(new URL('../../site-analytics.css', import.meta.url), 'utf8');
const production = 'https://b16171821.github.io/gpt-ai-assistant/';
const key = 'main-trend-analytics-consent-v1';
const events = page => page.evaluate(() => (window.dataLayer || []).map(x => Array.from(x)));
const views = async page => (await events(page)).filter(x => x[0] === 'event');

async function setup(page, url = production, options = {}) {
  const requests = [];
  await page.addInitScript(({ consent, brokenStorage, dnt }) => {
    if (consent) localStorage.setItem('main-trend-analytics-consent-v1', consent);
    if (brokenStorage) {
      Storage.prototype.getItem = () => { throw new Error('Storage unavailable'); };
      Storage.prototype.setItem = () => { throw new Error('Storage unavailable'); };
    }
    Object.defineProperty(navigator, 'doNotTrack', { get: () => dnt ? '1' : null });
  }, options);
  await page.route('**/*', async route => {
    const request = route.request();
    if (request.isNavigationRequest()) {
      await route.fulfill({ contentType: 'text/html; charset=utf-8', body: `<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>${css}</style></head><body>
        <header class="appHeader"><div id="workspaceTabs" data-page="0"></div></header>
        <input aria-label="投資資金"><input aria-label="股票搜尋">
        <script>${script}</script></body></html>` });
    } else {
      requests.push(request.url());
      if (options.networkFailure) await route.abort();
      else await route.fulfill({ contentType: 'text/javascript', body: '' });
    }
  });
  await page.goto(url);
  return requests;
}

async function changePage(page, index) {
  await page.evaluate(value => {
    document.getElementById('workspaceTabs').dataset.page = String(value);
  }, index);
}

test('no Google traffic before consent; compact notice fits viewport', async ({ page }) => {
  const requests = await setup(page);
  await expect(page.getByRole('button', { name: '允許統計' })).toBeVisible();
  expect(requests).toEqual([]);
  expect(await events(page)).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('only allowlisted page views are sent once; inputs and URL secrets excluded', async ({ page }) => {
  const requests = await setup(page, production + '?private=123#stock-secret');
  await page.getByRole('button', { name: '允許統計' }).click();
  await expect.poll(() => requests.length).toBe(1);
  expect(requests[0]).toBe('https://www.googletagmanager.com/gtag/js?id=G-NHLL9S7CLR');
  await expect.poll(async () => (await views(page)).length).toBe(1);
  await changePage(page, 0);
  await page.getByRole('textbox', { name: '投資資金' }).fill('987654321');
  await page.getByRole('textbox', { name: '股票搜尋' }).fill('private-stock');
  await changePage(page, 1);
  await expect.poll(async () => (await views(page)).length).toBe(2);
  await changePage(page, 2);
  await expect.poll(async () => (await views(page)).length).toBe(3);
  await changePage(page, 'bad-route');
  await changePage(page, 2);
  const queue = await events(page);
  const config = queue.find(x => x[0] === 'config');
  expect(config[1]).toBe('G-NHLL9S7CLR');
  expect(config[2].send_page_view).toBe(false);
  expect(config[2].allow_google_signals).toBe(false);
  expect((await views(page)).map(x => x[2].page_title)).toEqual(['主升段觀察', '資金計算機', '策略追蹤']);
  expect(JSON.stringify(queue)).not.toMatch(/private|987654321|stock-secret/);
});

test('refusal persists and revocation blocks further page views', async ({ page }) => {
  const requests = await setup(page);
  await page.getByRole('button', { name: '拒絕統計' }).click();
  await page.reload();
  expect(requests).toEqual([]);
  await page.locator('#analyticsPreferences summary').click();
  await page.getByRole('button', { name: '允許統計' }).click();
  await expect.poll(async () => (await views(page)).length).toBe(1);
  await page.locator('#analyticsPreferences summary').click();
  await page.getByRole('button', { name: '拒絕統計' }).click();
  await changePage(page, 1);
  expect((await views(page)).length).toBe(1);
  expect(await page.evaluate(() => window['ga-disable-G-NHLL9S7CLR'])).toBe(true);
  expect(await page.evaluate(k => localStorage.getItem(k), key)).toBe('denied');
});

test('saved consent tracks current page on reload', async ({ page }) => {
  await setup(page, production, { consent: 'granted' });
  await expect.poll(async () => (await views(page)).length).toBe(1);
  await page.reload();
  await expect.poll(async () => (await views(page)).length).toBe(1);
});

test('browser privacy signal wins over stored consent', async ({ page }) => {
  const requests = await setup(page, production, { consent: 'granted', dnt: true });
  expect(requests).toEqual([]);
  expect(await events(page)).toEqual([]);
});

test('storage denial and Google failure do not break the page', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await setup(page, production, { brokenStorage: true, networkFailure: true });
  await page.getByRole('button', { name: '允許統計' }).click();
  await changePage(page, 1);
  await page.getByRole('textbox', { name: '投資資金' }).fill('500000');
  await expect(page.getByRole('textbox', { name: '投資資金' })).toHaveValue('500000');
  expect(errors).toEqual([]);
});

test('local and unrelated pages do not enable analytics', async ({ page }) => {
  const requests = await setup(page, 'http://127.0.0.1:4173/index.html');
  expect(await page.locator('#analyticsPreferences').count()).toBe(0);
  expect(requests).toEqual([]);
  await page.goto('https://b16171821.github.io/other-site/');
  expect(await page.locator('#analyticsPreferences').count()).toBe(0);
  expect(requests).toEqual([]);
});

test('actual application tabs and form remain usable with production analytics', async ({ page }) => {
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin + '/' === 'https://b16171821.github.io/' && url.pathname.startsWith('/gpt-ai-assistant/')) {
      const localPath = url.pathname.slice('/gpt-ai-assistant/'.length) || 'index.html';
      const response = await route.fetch({ url: 'http://127.0.0.1:4173/' + localPath + url.search });
      await route.fulfill({ response });
    } else await route.fulfill({ contentType: 'text/javascript', body: '' });
  });
  await page.goto(production + '#tracking');
  await expect(page.getByRole('button', { name: '允許統計' })).toBeVisible();
  await page.screenshot({ path: test.info().outputPath('analytics-consent.png') });
  await page.getByRole('button', { name: '允許統計' }).click();
  await expect.poll(async () => (await views(page)).length).toBe(1);
  expect((await views(page))[0][2].page_title).toBe('策略追蹤');
  await page.getByRole('tab', { name: '資金計算機' }).click();
  await expect(page.locator('#capital')).toBeVisible();
  await page.locator('#capital').fill('654321');
  await page.getByRole('tab', { name: '主升段觀察' }).click();
  await expect.poll(async () => (await views(page)).length).toBe(3);
  await expect(page.locator('#pageTrack')).toHaveCSS('transform', 'matrix(1, 0, 0, 1, 0, 0)');
  expect(JSON.stringify(await events(page))).not.toContain('654321');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: test.info().outputPath('analytics-integrated.png') });
});
