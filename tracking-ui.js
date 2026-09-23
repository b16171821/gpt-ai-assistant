/* Display and risk overlays. Scanner outputs and locked strategies are read-only. */
const trackingQualityApi = window.TrackingQuality;
let trackingQualityData = { records: {} };
let trackingContextData = { records: {} };
const trackingListState = {
  query: '',
  filter: 'ALL',
  sort: 'default',
  showAll: false,
  hideLowProfit: true,
  profit: false,
  safety: false,
  rr: false,
  limits: {},
  quoteProvider: 'yahooTW',
};
const trackingGroups = ['main', 'reserve', 'hidden', 'ended', 'invalid', 'legacy'];

function qualityContext(record) {
  return trackingContextData.records?.[`${record.market}:${record.stockCode}`];
}
function qualityTrackingView(record) {
  const meta = marketMetas[record.market] || {};
  const marketDate = meta.officialDataDate || meta.completedDataDate || meta.strategyAsOfDate;
  const recordDate = record.latestStatus?.latestDataDate || record.lastUpdateDate;
  const regime = marketDate && marketDate >= recordDate ? marketFilterFor(record.market).marketRegime : undefined;
  const v = trackingQualityApi.view(record, qualityContext(record), regime);
  if (!v.terminal && marketDate && (!recordDate || recordDate < marketDate)) {
    v.highQualityTracking = false; v.highPriority = false;
    v.tier = 'RESERVE'; v.label = '備查'; v.reason = '收盤資料待更新，暫不列主追蹤。';
    v.policy = {
      ...v.policy,
      allowEntry: false,
      strongObservation: false,
      maxPositionPercent: 0,
      signal: '資料待更新',
      cashAction: '等待完整收盤資料。',
    };
  }
  const riskRow = attachDispositionRisk({ marketName: record.market, code: record.stockCode });
  const risk = dispositionRiskOf(riskRow);
  v.tradeRiskLabel = risk ? dispositionInfo(riskRow).label : '';
  if (risk && !riskEligible(riskRow)) {
    v.policy = {
      ...v.policy,
      allowEntry: false,
      maxPositionPercent: 0,
      signal: v.policy.signal === '小部位試單' ? '交易風險觀察' : v.policy.signal,
      cashAction: risk.tradeRecommendation || '交易風險待確認，不列正常進場候選。',
    };
  }
  return v;
}
function qualityPercent(value, signed = false) {
  if (value === null || !Number.isFinite(value)) return '—';
  return `${(signed && value > 0 ? '+' : '') + value.toFixed(2)}%`;
}
function qualityTargetDistanceText(record) {
  if (record.trackingStatus === 'TARGET_HIT') return '已達第一目標';
  if (record.trackingStatus === 'NEED_REVIEW') return '同日觸發，需人工確認';
  if (trackingQualityApi.TERMINAL.has(record.trackingStatus)) return '策略已結束';
  const target = Number(record.originalStrategy?.originalTarget);
  const close = Number(record.latestStatus?.latestClose);
  if (!Number.isFinite(target) || !Number.isFinite(close) || target <= 0 || close <= 0) {
    return '距第一目標：資料不足';
  }
  if (close >= target) return '收盤已達第一目標，待確認追蹤結果';
  const remaining = ((target - close) / close) * 100;
  if (!Number.isFinite(remaining)) return '距第一目標：資料不足';
  return `距第一目標尚有：${qualityPercent(remaining)}`;
}
function qualityStatusClass(label) {
  if (['目標達成', '接近目標', '停利警戒'].includes(label)) return 'profit';
  if (label === '高優先') return 'gradeA';
  if (label === '持股風控') return 'qualityRiskBadge';
  if (label === '停損結束') return 'gradeC';
  if (label === '強勢觀察') return 'gradeB';
  return 'watch';
}
function trackingSignalMeaning(value, kind = 'signal') {
  const signal = String(value || '');
  if (!signal || signal === '-') return '資料未提供，不推測訊號或階段。';
  const stages = {
    '突破第一根': '該資料日被原策略判為突破階段；不表示之後每天都維持突破，也不是目前可直接進場。',
    '回踩頸線不破': '原策略判定曾突破後回到頸線附近，符合當時的回踩條件；仍須看最新收盤與原始停損。',
    '安全回踩': '當時屬回踩觀察類型；「安全」是分類名稱，不代表沒有跌破停損的風險。',
    '主升候選': '進入主升段候選觀察，仍須符合原策略確認條件，並非已完成主升段。',
    '接近成形＋可觀察': '型態接近成形，但尚未被原策略判為突破或回踩確認。',
    '可觀察': '條件仍在觀察階段，不能等同已確認買點。',
    '接近停利': '價格已接近策略目標，重點轉向持股風控，不是新的追價訊號。',
    '禁止追高': '當時價格位置不適合追價；保留研究，不代表進場條件成立。',
    '資料不足': '必要資料不完整，無法確認型態。',
  };
  if (kind === 'stage') return stages[signal] || '此為建立策略時保存的型態描述，不等於目前追蹤狀態；未提供更細的判定說明。';
  const signals = {
    '突破買點': '原策略標示突破型買點；這是技術訊號，仍須對照收盤確認、觀察區與分級後限制，不代表立即買入。',
    '回踩買點': '原策略標示回踩型買點；確認價格是否守住頸線或觀察區，不能只因價格下跌就視為回踩成功。',
    '正式進場': '原策略的訊號名稱，不是成交紀錄；仍以分級後訊號、有效價位與交易風險為準。',
    '接近目標': '價格靠近目標，持有者優先留意停利；空手者不要把它當作新的進場訊號。',
    '觀察': '保留追蹤，尚不能視為確認進場。',
    '等待確認': '條件尚未完成或買點被降級，須等後續收盤與原策略條件確認。',
    '小部位試單': '觀察盤下的回踩觀察訊號，依原策略確認後才評估；策略部位上限與計算機自訂數值不同。',
    '防守盤強勢觀察': '符合相對強勢觀察條件，但防守盤仍不列正式進場；等待大盤止跌或轉觀察盤。',
    '等待大盤止跌': '個股繼續追蹤，但大盤防守限制尚未解除，不是正式進場訊號。',
    '抗跌追蹤': '相對強勢只作追蹤用途；停止進場盤不開新倉。',
    '持股風控': '重點是既有持股的停損與停利管理，不是新買點或加碼訊號。',
    '資料待更新': '收盤資料落後於目前市場資料日，先等待更新，不據此確認進場。',
    '交易風險觀察': '交易風險尚未符合正常進場條件，即使技術訊號存在也不列正常候選。',
    '已漲遠或接近目標，空手不追；持有者依策略分批處理。': '價格位置已不適合空手者追價；已有持股依原始目標及停損管理，不表示策略已經達標。',
    '回踩頸線不破，可觀察低風險進場': '觀察重點是回踩時守住頸線，不是看到跌價就進場；低風險指價位安排，不保證不會虧損。',
    '條件或風險不夠好，先不列入高關注。': '條件或風險尚未達到高關注要求，保留備查，不屬優先進場訊號。',
    '條件接近完整，等收盤確認或回踩不破。': '尚在確認前，等待收盤站穩或原策略的回踩條件成立。',
    '距離頸線或前高過遠，禁止追高': '目前價格離參考位置過遠，不以此訊號追價，也不提高原始停損或目標來合理化進場。',
    '預備單，等待確認條件': '這是待確認的觀察策略，不代表已掛單、成交或允許立即買入。',
  };
  return signals[signal] || '此為資料保存的策略描述；未提供更細的條件明細，不能單憑名稱推定已確認進場。';
}
function trackingStageLabel(record) {
  return {
    WATCHING: '追蹤中', WAIT_CONFIRM: '等待確認', BREAKOUT_CONFIRMED: '突破確認',
    PULLBACK_CONFIRM: '回踩確認', IN_TREND: '主升段進行中', NEAR_TARGET: '接近目標',
    TARGET_HIT: '目標達成', FAILED: '停損結束', EXPIRED: '觀察過期',
    NEED_REVIEW: '同日觸發待確認', INVALID_AT_CREATION: '無效建立', MANUAL_CLOSED: '手動結束',
  }[record.trackingStatus] || '階段待確認';
}
function trackingSignalSection(record, v) {
  const o = record.originalStrategy || {}; const l = record.latestStatus || {};
  const lifecycle = {
    WATCHING: '追蹤中，尚未確認突破或結束。',
    WAIT_CONFIRM: '接近原策略買點，仍在等待收盤確認。',
    BREAKOUT_CONFIRMED: '追蹤系統記錄為突破確認；是否能開新倉仍受分級訊號限制。',
    PULLBACK_CONFIRM: '追蹤系統記錄為回踩確認；不代表大盤已允許新進場。',
    IN_TREND: '原策略已進入趨勢追蹤，持續檢查原始停損與第一目標。',
    NEAR_TARGET: '接近原始目標，優先留意停利；接近不等於已達標。',
    TARGET_HIT: '原始第一目標已達成，策略結束；後續回跌不改寫達標結果。',
    FAILED: '原始策略已失敗，不可用新的策略卡延後停損。',
    EXPIRED: '原策略已過期，這筆策略不再作為有效進場依據。',
    NEED_REVIEW: '同日觸及目標與停損，日線無法確定先後，不能判成單純成功或失敗。',
    INVALID_AT_CREATION: '建立時已不符合有效追蹤條件，不計為正式達標或停損樣本。',
    MANUAL_CLOSED: '此策略已手動結束，後續行情不重新開啟原策略。',
  };
  const terminal = v.terminal;
  const rows = [
    ['原始訊號（建立時，固定）', o.originalSignal || '未提供', `資料日：${trackingDataDate(record)}。${trackingSignalMeaning(o.originalSignal)} 後續新訊號不覆蓋原始買入區、停損或目標。`],
    ['目前技術訊號（最新資料）', l.latestSignal || '未提供', `資料日：${l.latestDataDate || record.lastUpdateDate || '未提供'}。${trackingSignalMeaning(l.latestSignal)} 與原始訊號不同，表示目前判讀已變動，不會重設舊策略。`],
    ['分級後訊號（目前限制）', terminal ? '原策略已結束' : v.policy.signal, terminal ? '原策略不再產生新進場依據；新的技術訊號不能重新開啟這筆追蹤。' : `${trackingSignalMeaning(v.policy.signal)} 大盤：${v.policy.regime}；${v.policy.label}。${v.tradeRiskLabel ? `交易風險：${v.tradeRiskLabel}。` : ''} 空手者：${v.policy.cashAction}`],
    ['原始型態階段（建立時）', o.originalStage || '未提供', trackingSignalMeaning(o.originalStage, 'stage')],
    ['目前追蹤階段（生命週期）', trackingStatusText[record.trackingStatus] || record.trackingStatus || '未提供', lifecycle[record.trackingStatus] || '未提供此追蹤狀態的完整說明，不推測是否已確認。'],
  ];
  return `<section class="trackingSignalSection" aria-label="訊號與階段說明"><dl class="qualityCompactFields">${rows.slice(0, 3).map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join('')}</dl><details class="qualitySignalHelp"><summary>訊號與階段完整說明</summary><dl>${rows.map(([label, value, meaning]) => `<div><dt>${esc(label)}</dt><dd><b>${esc(value)}</b><p>${esc(meaning)}</p></dd></div>`).join('')}</dl><p class="trackingSignalRule">技術訊號不等於進場許可；空手者看分級後限制，持有者仍守原始停損與目標。高優先與安全分是篩選標籤，不是獲利保證。</p></details></section>`;
}
function qualityTrackingCard(record) {
  const v = qualityTrackingView(record); const o = record.originalStrategy || {}; const
    l = record.latestStatus || {};
  const u = trackingUnit(record); const
    i = trackingData.records.indexOf(record);
  const badges = [v.label];
  if (v.highPriority && v.label !== '高優先') badges.push('高優先');
  const detailId = `qualityDetail-${i}`;
  const fields = [
    ['最新收盤', trackingPrice(l.latestClose, u)],
    ['預期獲利', qualityPercent(v.expectedProfitPercent, true)],
    [v.terminal ? '策略價位績效' : '目前績效', trackingProfitText(record)],
    ['停損距離', v.stopLossDistancePercent === null ? '—' : v.stopLossDistancePercent <= 0 ? '價位無效' : `-${qualityPercent(v.stopLossDistancePercent)}`],
    ['原始目標', trackingPrice(o.originalTarget, u)],
    ['原始停損', trackingPrice(o.originalStopLoss, u)],
    ['原始頸線', trackingPrice(o.originalNeckline, u)],
    ['風報比', fmt(v.riskReward)],
    ['安全分', fmt(v.safetyScore, 0)],
  ];
  const endedInfo = v.terminal ? trackingEndedInfo(record, u, v.label) : '';
  const admission = trackingQualityData.records?.[record.trackingId];
  const cohortText = admission?.basis === 'RECORDED_AT_CREATION'
    ? (admission.eligible ? '建立時符合正式高品質樣本' : '建立時未符合正式高品質樣本')
    : '歷史紀錄：缺少事前固定的品質資格，不計入新制正式達成率';
  const actions = v.terminal ? '<div class="muted small">原始策略已結束；後續行情不改寫結果。</div>'
    : `<div class="trackingLine">持有者：${esc(v.policy.holderAction)}<br>空手者：${esc(v.policy.cashAction)}</div>`;
  return `<article class="trackingCard qualityCard ${v.highPriority ? 'qualityPriority' : ''}" data-tracking-id="${esc(record.trackingId)}" data-tier="${v.tier}">
    <div class="trackingTop"><b class="trackingName">${esc(record.stockName || record.stockCode)}（${esc(record.stockCode)}）</b><div class="trackingBadges">${badges.map((label) => `<span class="pill ${qualityStatusClass(label)}">${esc(label)}</span>`).join('')}</div></div>
    <div class="qualityStageRow"><div><span>目前階段</span><b>${esc(trackingStageLabel(record))}</b></div><div><span>建立時型態</span><b>${esc(o.originalStage || '未提供')}</b></div></div>
    <div class="qualityBuyZone"><span>原始買入觀察區</span><b>${fmt(o.originalBuyZoneLow)}～${fmt(o.originalBuyZoneHigh)} ${u}</b></div>
    <div class="qualityMetrics">${fields.map(([label, value]) => `<div><span>${label}</span><b>${value}</b></div>`).join('')}</div>
    <div class="qualityReason">${esc(v.reason)}</div>
    <div class="qualityMarket">大盤：${esc(v.policy.regime)}${v.policy.regime === 'WATCH' ? '｜買點降級' : ''}${v.tradeRiskLabel && v.tradeRiskLabel !== '正常交易' ? `｜${esc(v.tradeRiskLabel)}` : ''}</div>
    <div class="muted small">${v.terminal ? `結束日：${esc(record.endedAt || record.endDate || '-')}` : `收盤資料：${esc(l.latestDataDate || record.lastUpdateDate || '-')}`}</div>
    <div class="qualityCardActions"><a class="quoteButton" href="${esc(trackingQualityApi.quoteUrl(record, trackingListState.quoteProvider))}" target="_blank" rel="noopener noreferrer">查看股價<span class="srOnly">（另開分頁）</span></a><button type="button" class="positionButton" onclick="openTrackingPositionCalculator(${i})">帶入部位計算</button></div>
    <details id="${detailId}" class="qualityDetails"><summary>展開完整策略</summary><div class="trackingDetail">
    <dl class="qualityCompactFields qualityDates"><div><dt>原始策略日</dt><dd>${esc(trackingDataDate(record))}</dd></div><div><dt>加入追蹤日</dt><dd>${esc(trackingOrigin(record) === 'LEGACY_IMPORT' ? '歷史回補' : trackingCreatedDate(record))}</dd></div></dl>
    ${trackingSignalSection(record, v)}
    <div class="trackingSubsection qualityCurrent"><b>目前判讀</b><div class="qualityDistance">距頸線：${qualityPercent(v.distanceToNecklinePercent, true)}<span>${esc(qualityTargetDistanceText(record))}</span></div>${actions}</div>
    ${record.trackingStatus === 'FAILED' ? `<div class="trackingAlert danger">${failedStrategyNotice}</div>` : ''}
    ${endedInfo}${trackingProgressTags(record.progress, record.trackingStatus)}
    <details class="qualityNotes"><summary>原始鎖定資料與計算口徑</summary>${originalStrategyHtml(record, u)}<p class="muted small">預期獲利基準：原始觀察區下緣；缺少時用最新收盤。停損距離使用原始觀察區下緣／觀察價。目前績效以原始觀察區中間值試算，未計成本，不代表實際成交績效。</p><div class="trackingLine">統計資格：${esc(cohortText)}</div>${v.policy.regime === 'DEFENSE' || v.policy.regime === 'STOP' ? `<div class="trackingLine">強勢證據：${qualityContext(record)?.status === 'COMPLETE' ? `個股近5日 ${qualityPercent(qualityContext(record).stockReturn5Pct, true)}｜大盤 ${qualityPercent(qualityContext(record).marketReturn5Pct, true)}` : '同期間資料不足，不判定強勢例外'}</div>` : ''}</details>
    <details class="qualityNotes"><summary>備註與歷史紀錄</summary>${trackingNotesHtml(record.notes)}${trackingLegacySummary(record)}</details>
    <div class="copyBtns"><button class="secondary" type="button" onclick="copyTrackingStrategy(${i})">複製完整策略</button></div><span id="tc${i}" class="green small"></span>
    </div></details></article>`;
}
function trackingGroupRecords() {
  const records = trackingData?.records || [];
  const groups = Object.fromEntries(trackingGroups.map((group) => [group, []]));
  for (const record of records) {
    const v = qualityTrackingView(record);
    let group = v.terminal ? 'ended' : v.tier === 'MAIN' ? 'main' : v.tier === 'HIDDEN' ? 'hidden' : 'reserve';
    if (trackingQualityApi.origin(record) === 'LEGACY_IMPORT') group = 'legacy';
    else if (record.trackingStatus === 'INVALID_AT_CREATION') group = 'invalid';
    groups[group].push({ record, view: v });
  }
  return groups;
}
function filterTrackingItems(items) {
  const s = trackingListState;
  return items.filter(({ record, view: v }) => trackingQualityApi.matches(record, v, s.query, s.filter)
    && (!(s.hideLowProfit || s.profit) || v.expectedProfitPercent >= 15)
    && (!s.safety || v.safetyScore >= 90) && (!s.rr || v.riskReward >= 3))
    .sort((a, b) => trackingQualityApi.compare(a, b, s.sort));
}
function renderQualityGroup(group) {
  const items = trackingGroupRecords()[group];
  // Search applies to every archive; numeric filters describe active opportunities only.
  const filtered = ['main', 'reserve', 'hidden'].includes(group) ? filterTrackingItems(items)
    : items.filter(({ record }) => !trackingListState.query || (`${record.stockCode} ${record.stockName}`).toLowerCase().includes(trackingListState.query.toLowerCase()))
      .sort((a, b) => String(b.record.endedAt || b.record.firstSignalDate).localeCompare(String(a.record.endedAt || a.record.firstSignalDate)));
  const limit = trackingListState.limits[group] || 15;
  const root = document.getElementById(`quality-${group}-list`);
  const count = document.getElementById(`quality-${group}-count`);
  if (!root) return;
  if (count) count.textContent = `${filtered.length} / ${items.length} 檔`;
  const container = document.getElementById(`quality-${group}`);
  if (container?.tagName === 'DETAILS' && !container.open) { root.innerHTML = ''; return; }
  root.innerHTML = filtered.length ? filtered.slice(0, limit).map(({ record }) => qualityTrackingCard(record)).join('')
    : '<div class="empty">沒有符合目前篩選的紀錄。</div>';
  const more = document.getElementById(`quality-${group}-more`);
  if (more) {
    more.hidden = filtered.length <= limit;
    more.textContent = `載入更多（已顯示 ${Math.min(limit, filtered.length)} / ${filtered.length}）`;
  }
}
function renderQualityTracking() {
  const records = trackingData?.records || []; const
    groups = trackingGroupRecords();
  const stats = trackingQualityApi.statistics(records, trackingQualityData.records);
  const rate = stats.targetRate === null ? '樣本不足' : `${stats.targetRate.toFixed(1)}%`;
  document.getElementById('trackingUpdated').textContent = trackingData.meta?.err ? `追蹤資料讀取失敗：${trackingData.meta.err}` : `收盤追蹤更新：${twTime(trackingData.meta?.updatedAt)}`;
  document.getElementById('qualityStats').innerHTML = [
    ['高品質進行中', groups.main.length], ['正式目標達成', stats.hit], ['正式停損結束', stats.failed], ['目標達成率', rate],
  ].map(([label, value]) => `<div><span>${label}</span><b>${value}</b></div>`).join('');
  document.getElementById('trackingResultSummary').textContent = `全部 ${records.length
  }｜備查 ${groups.reserve.length + groups.hidden.length}｜歷史回補 ${groups.legacy.length}｜無效建立 ${groups.invalid.length}｜已結束 ${groups.ended.length}`;
  document.getElementById('qualityStatisticsNote').textContent = `正式達成率 = 目標達成 ÷（目標達成＋停損結束＋有效策略觀察過期）。資格在建立時固定，結果不因篩選或後來降級而移除。已結束樣本 ${stats.resolved}；至少 10 筆才顯示比率。新制正式樣本進行中 ${stats.active}，有效策略過期 ${stats.expired}。歷史回補、舊資料未事前記錄資格、無效建立、建立時僅觀察、同日待確認與手動結束另列，不混入正式達成率。`;
  document.getElementById('quality-hidden').hidden = !trackingListState.showAll;
  document.getElementById('qualityHiddenHint').textContent = `低獲利／風報比不足等隱藏紀錄 ${groups.hidden.length} 檔；開啟「顯示全部」可查閱。`;
  document.querySelectorAll('[data-quality-filter]').forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.qualityFilter === trackingListState.filter)));
  trackingGroups.forEach(renderQualityGroup);
}
function qualityFilterChanged() {
  trackingListState.limits = {};
  renderQualityTracking();
}
function initQualityTracking() {
  const search = document.getElementById('trackingSearch');
  search.addEventListener('input', () => { trackingListState.query = search.value.trim(); qualityFilterChanged(); });
  document.getElementById('trackingSort').addEventListener('change', (event) => { trackingListState.sort = event.target.value; qualityFilterChanged(); });
  document.querySelectorAll('[data-quality-filter]').forEach((button) => button.addEventListener('click', () => { trackingListState.filter = button.dataset.qualityFilter; qualityFilterChanged(); }));
  document.querySelectorAll('[data-quality-toggle]').forEach((input) => input.addEventListener('change', () => {
    trackingListState[input.dataset.qualityToggle] = input.checked;
    if (input.dataset.qualityToggle === 'showAll') {
      trackingListState.hideLowProfit = !input.checked;
      document.querySelector('[data-quality-toggle="hideLowProfit"]').checked = !input.checked;
    }
    qualityFilterChanged();
  }));
  trackingGroups.forEach((group) => {
    document.getElementById(`quality-${group}`)?.addEventListener('toggle', () => renderQualityGroup(group));
    document.getElementById(`quality-${group}-more`)?.addEventListener('click', () => {
      trackingListState.limits[group] = (trackingListState.limits[group] || 15) + 15;
      renderQualityGroup(group);
    });
  });
  document.getElementById('trackingTopButton').addEventListener('click', () => document.getElementById('trackingPage').scrollTo({ top: 0, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' }));
}
function homeQualityView(row) {
  const record = trackingQualityApi.fromStock(row);
  const v = trackingQualityApi.view(record, qualityContext(record), marketFilterFor(row.marketName).marketRegime);
  if (row.dataFresh === false || row.grade === 'C' || row.forbiddenChase) v.highQualityTracking = false;
  return v;
}
function homeDisplayRow(row, main = false) {
  const v = homeQualityView(row); const
    blocked = row.marketName === '台股' && !riskEligible(row);
  const label = blocked ? '交易風險待確認' : v.policy.signal;
  return {
    ...row,
    mainPick: main && v.policy.regime === 'ATTACK',
    adjustedSignal: label,
    action: label,
    nextAction: label,
    cashAction: blocked ? dispositionRiskOf(row)?.tradeRecommendation || '等待交易風險確認。' : v.policy.cashAction,
    emptyAction: blocked ? dispositionRiskOf(row)?.tradeRecommendation || '等待交易風險確認。' : v.policy.cashAction,
    holderAction: v.policy.holderAction,
    marketRegimeImpact: v.policy.label,
    entryCondition: v.policy.allowEntry && !blocked ? row.entryCondition : '保留原始價位觀察，等待大盤與個股條件確認。',
    riskWarning: v.policy.label,
    riskText: v.policy.label,
  };
}
function renderQualityHome() {
  renderMarketPanel(); renderDataHealth(); renderRiskRadar();
  const sorted = rows().slice().sort((a, b) => scoreKey(b) - scoreKey(a));
  const high = sorted.filter((row) => homeQualityView(row).highQualityTracking && riskEligible(row));
  const regime = currentRegime();
  const main = high.filter((row) => ['ATTACK', 'WATCH'].includes(regime) && !homeQualityView(row).nearTarget).slice(0, 5);
  const strong = high.filter((row) => homeQualityView(row).policy.strongObservation).slice(0, 5);
  const selected = new Set([...main, ...strong].map(key));
  const show = (id, countId, items, empty, ranked) => {
    document.getElementById(countId).textContent = `${items.length} 檔`;
    document.getElementById(id).innerHTML = items.length ? items.map((r, rank) => card(homeDisplayRow(r, ranked), r._i, rank + 1)).join('') : `<div class="empty">${empty}</div>`;
  };
  show('mainPickList', 'mainPickN', main, '目前沒有符合品質與進場分級的標的；可查看強勢觀察及備查。', true);
  show('strongPickList', 'strongPickN', strong, '目前沒有同時符合強勢條件及完整資料的標的。', false);
  document.getElementById('tradablePick').hidden = true;
  const rest = sorted.filter((row) => !selected.has(key(row)));
  put('gradeAList', 'gradeAN', rest.filter((r) => r.grade === 'A').map((r) => homeDisplayRow(r)), 8);
  put('pullbackList', 'pullbackN', rest.filter((r) => r.category.includes('回踩') && r.grade !== 'C').map((r) => homeDisplayRow(r)), 6);
  put('gradeBList', 'gradeBN', rest.filter((r) => r.grade === 'B').map((r) => homeDisplayRow(r)), 8);
  put('nochaseList', 'nochaseN', rest.filter((r) => r.grade === 'C').map((r) => homeDisplayRow(r)), 10);
  put('profitList', 'profitN', rest.filter((r) => r.nearTarget).map((r) => homeDisplayRow(r)), 6);
  const policyText = {
    ATTACK: '進攻盤：符合品質條件者優先確認原始買點。',
    WATCH: '觀察盤：突破等確認；回踩可試單，試算部位上限 10%。',
    DEFENSE: '防守盤：保留強勢觀察，空手等待大盤止跌。',
    STOP: '停止進場：保留抗跌追蹤與持股風控，不開新倉。',
    NO_DATA: '等待完整大盤資料，持股仍守原始停損。',
  };
  document.querySelector('#marketFilterBox .strategyAdvice').textContent = `一句策略建議：${policyText[regime] || policyText.NO_DATA}`;
  const stats = document.querySelectorAll('#marketFilterBox .regimeStat b');
  if (stats[2]) stats[2].textContent = main.length + strong.length;
}
async function loadTrackingQuality() {
  const results = await Promise.allSettled([j('data/tracking_quality.json'), j('data/tracking_context.json')]);
  trackingQualityData = results[0].status === 'fulfilled' ? results[0].value : { records: {} };
  trackingContextData = results[1].status === 'fulfilled' ? results[1].value : { records: {} };
}
function applySelectedQualityLimit() {
  const selected = calculatorState.selectedStock;
  if (!selected) return;
  const record = selected.source === 'tracking' ? (trackingData.records || []).find((r) => r.trackingId === selected.trackingId) : null;
  const row = !record ? all.find((r) => key(r) === selected.key) : null;
  if (!record && !row) return;
  const v = record ? qualityTrackingView(record) : homeQualityView(row);
  const ceiling = v.terminal || (row && !riskEligible(row)) ? 0 : v.policy.maxPositionPercent;
  // Calculator defaults/preferences are independent of the strategy's market limit.
  return `${v.policy.label}；策略部位上限 ${ceiling}%。計算機使用自訂上限，僅供數值試算，不代表允許進場。`;
}
