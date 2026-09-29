(() => {
  'use strict';
  const id = 'G-NHLL9S7CLR';
  const base = 'https://b16171821.github.io/gpt-ai-assistant/';
  if (location.protocol !== 'https:' || location.hostname !== 'b16171821.github.io'
    || !['/gpt-ai-assistant/', '/gpt-ai-assistant/index.html'].includes(location.pathname)
    || document.getElementById('analyticsPreferences')) return;

  const tabs = document.getElementById('workspaceTabs');
  const header = document.querySelector('.appHeader');
  if (!tabs || !header) return;
  const key = 'main-trend-analytics-consent-v1';
  const pages = [
    ['analysis', '主升段觀察'], ['calculator', '資金計算機'], ['tracking', '策略追蹤'],
  ];
  let consent = false;
  let initialized = false;
  let lastPage = null;
  let saved = null;
  try { saved = localStorage.getItem(key); } catch (_) { /* Session-only consent. */ }
  const privacySignal = navigator.globalPrivacyControl === true || navigator.doNotTrack === '1';
  let referrer = '';
  try { referrer = document.referrer ? new URL(document.referrer).origin : ''; } catch (_) { /* No referrer. */ }
  function pageInfo() {
    if (!/^[012]$/.test(tabs.dataset.page)) return null;
    const page = pages[tabs.dataset.page];
    return page ? { page_location: base + page[0], page_title: page[1], page_referrer: referrer } : null;
  }
  function send() { window.dataLayer.push(arguments); }
  function trackPage() {
    const info = pageInfo();
    if (!consent || !info || lastPage === info.page_location) return;
    send('event', 'page_view', { ...info, send_to: id });
    lastPage = info.page_location;
  }
  function enable() {
    consent = true;
    window['ga-disable-' + id] = false;
    if (!initialized) {
      initialized = true;
      window.dataLayer = window.dataLayer || [];
      window.gtag = send;
      send('consent', 'default', {
        analytics_storage: 'granted', ad_storage: 'denied',
        ad_user_data: 'denied', ad_personalization: 'denied',
      });
      send('js', new Date());
      send('config', id, {
        ...pageInfo(), send_page_view: false,
        allow_google_signals: false, allow_ad_personalization_signals: false,
        cookie_path: '/gpt-ai-assistant/', cookie_domain: 'none',
      });
      const script = document.createElement('script');
      script.async = true;
      script.src = 'https://www.googletagmanager.com/gtag/js?id=' + id;
      document.head.append(script);
    } else {
      send('consent', 'update', { analytics_storage: 'granted' });
    }
    trackPage();
  }
  const preferences = document.createElement('details');
  preferences.id = 'analyticsPreferences';
  preferences.className = 'analyticsPreferences';
  preferences.open = false;
  preferences.innerHTML = `<summary>統計設定</summary>
    <p>本站使用 Google Analytics 與 Cookie 自動統計訪客及三個主要頁面，不傳送試算金額、股票搜尋或個股操作內容。可隨時拒絕統計，不影響網站功能。</p>
    <p>累積訪客依 GA 去重統計，並非精確真人數；跨裝置、封鎖器及資料處理延遲可能影響人數。總數定時更新，不是即時在線人數。</p>
    <a href="https://policies.google.com/privacy?hl=zh-TW" target="_blank" rel="noopener noreferrer">Google 隱私權政策</a>
    <div class="analyticsActions"><button type="button" data-consent="granted">允許統計</button><button type="button" data-consent="denied">拒絕統計</button></div>
    <p class="analyticsStatus" role="status"></p>`;
  const bar = document.createElement('div');
  bar.className = 'analyticsBar';
  const counter = document.createElement('span');
  counter.id = 'siteVisitorCount';
  counter.setAttribute('role', 'status');
  counter.textContent = '累積訪客：讀取中';
  bar.append(counter, preferences);
  header.append(bar);
  // Read only the public aggregate; credentials and visitor identifiers never enter the page.
  fetch('https://raw.githubusercontent.com/b16171821/gpt-ai-assistant/main/data/site_visitors.json', {
    cache: 'no-store', credentials: 'omit', signal: AbortSignal.timeout(8000),
  }).then(response => {
    if (!response.ok) throw new Error('Statistics unavailable');
    return response.json();
  }).then(data => {
    if (data.status === 'PENDING_CONFIGURATION') {
      counter.textContent = '累積訪客：待設定';
      counter.title = '尚未接通 GA 唯讀報表，不以瀏覽次數或假數字替代人數。';
      return;
    }
    const updated = Date.parse(data.updatedAt);
    if (data.status !== 'OK' || data.metric !== 'totalUsers'
      || !Number.isSafeInteger(data.totalUsers) || data.totalUsers < 0
      || !Number.isFinite(updated) || updated > Date.now() + 300000) throw new Error('Invalid statistics');
    const stale = Date.now() - updated > 86400000;
    counter.textContent = `累積訪客：約 ${data.totalUsers.toLocaleString('zh-TW')} 人${stale ? '（待更新）' : ''}`;
    counter.title = `統計期間：${data.startDate}～${data.throughDate}；更新：${new Date(updated).toLocaleString('zh-TW', { timeZone: 'Asia/Taipei' })}；來源：Google Analytics 4`;
  }).catch(() => { counter.textContent = '累積訪客：暫無資料'; });
  const status = preferences.querySelector('.analyticsStatus');
  function showStatus() {
    status.textContent = privacySignal ? '瀏覽器已要求不追蹤，本次不啟用統計。'
      : consent ? '瀏覽統計已啟用，可隨時改為拒絕。' : '目前未啟用瀏覽統計。';
  }
  preferences.addEventListener('click', event => {
    const choice = event.target.closest('[data-consent]');
    if (!choice) return;
    const value = choice.dataset.consent;
    try { localStorage.setItem(key, value); } catch (_) { /* Keep the choice for this page. */ }
    if (value === 'granted' && !privacySignal) enable();
    else {
      consent = false;
      lastPage = null;
      window['ga-disable-' + id] = true;
      if (initialized) send('consent', 'update', { analytics_storage: 'denied' });
    }
    showStatus();
    preferences.open = false;
  });
  new MutationObserver(trackPage).observe(tabs, { attributes: true, attributeFilter: ['data-page'] });
  if (saved !== 'denied' && !privacySignal) enable();
  showStatus();
})();
