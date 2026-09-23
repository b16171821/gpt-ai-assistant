/* UI only: position-calculator.js remains the source of all numeric results. */
const calculatorPreferenceKey = 'positionCalculatorPreferences.v1';
const calculatorDefaultsVersion = 2;
const calculatorPreferenceFields = ['capital', 'riskPercent', 'maxPositionPercent', 'tradeUnit', 'feeBufferPercent'];
let calculatorPreferences = {};

function validCalculatorPreference(field, value) {
  if (value === '' || value === null || value === undefined) return false;
  const number = Number(value);
  if (!Number.isFinite(number)) return false;
  if (field === 'tradeUnit') return number === 1;
  return field === 'feeBufferPercent' ? number >= 0 : number > 0;
}

function saveCalculatorPreference(field, value) {
  if (!document.getElementById('rememberCalculator').checked || !calculatorPreferenceFields.includes(field)) return;
  if (validCalculatorPreference(field, value)) calculatorPreferences[field] = Number(value);
  else delete calculatorPreferences[field];
  try {
    localStorage.setItem(calculatorPreferenceKey, JSON.stringify({ remember: true, defaultsVersion: calculatorDefaultsVersion, values: calculatorPreferences }));
  } catch (error) {
    document.getElementById('calculatorStatus').textContent = '瀏覽器無法儲存設定，本次試算仍可使用。';
  }
}

function setQuickCalculatorField(field, value) {
  calculatorState = positionApi.updatePositionStateField(calculatorState, field, value);
  syncPositionInputs();
  saveCalculatorPreference(field, value);
  renderPositionCalculator();
}

function confirmPositionCalculation() {
  // Read the visible controls again to include autofill that emitted no input event.
  positionFieldNames.forEach((field) => {
    const input = document.getElementById(field);
    if (input) calculatorState = positionApi.updatePositionStateField(calculatorState, field, field === 'tradeUnit' ? Number(input.value) : input.value);
  });
  const result = renderPositionCalculator();
  const status = document.getElementById('positionConfirmStatus');
  if (!result.valid) {
    renderPositionErrors(result);
    const invalidFields = positionFieldNames.filter((field) => result.errors[field]);
    invalidFields.forEach((field) => document.getElementById(field)?.setAttribute('aria-invalid', 'true'));
    if (invalidFields.some((field) => ['riskPercent', 'maxPositionPercent', 'feeBufferPercent'].includes(field))) {
      document.getElementById('calcAdvanced').open = true;
    }
    status.textContent = calculatorState.maxPositionPercent !== '' && Number(calculatorState.maxPositionPercent) === 0
      ? '無法試算：部位上限為 0%，目前不開新倉。請查看「風險與成本設定」；確認按鈕不會解除此限制。'
      : `尚未完成試算：${result.errors[invalidFields[0]] || '請檢查輸入欄位。'}`;
    document.getElementById(invalidFields[0])?.focus();
    return result;
  }
  status.textContent = result.finalShares === 0
    ? `試算完成：0 股。${result.zeroReason}`
    : `試算完成：${formatShares(result.finalShares)}，預估投入 ${formatTwd(result.estimatedInvestment)}。`;
  document.getElementById('positionResults').scrollIntoView({ block: 'nearest' });
  return result;
}

function initSimpleCalculator() {
  const remember = document.getElementById('rememberCalculator');
  document.getElementById('confirmPosition').addEventListener('click', confirmPositionCalculation);
  document.getElementById('positionForm').addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && event.target.matches('input[type="number"]')) {
      event.preventDefault();
      confirmPositionCalculation();
    }
  });
  try {
    const stored = JSON.parse(localStorage.getItem(calculatorPreferenceKey) || '{}');
    remember.checked = stored?.remember !== false;
    if (remember.checked) {
      calculatorPreferenceFields.forEach((field) => {
        // Migrate only the changed defaults; retain the user's capital/risk inputs.
        if (stored.defaultsVersion !== calculatorDefaultsVersion && ['maxPositionPercent', 'feeBufferPercent', 'tradeUnit'].includes(field)) return;
        if (validCalculatorPreference(field, stored?.values?.[field])) {
          calculatorPreferences[field] = Number(stored.values[field]);
        }
      });
      calculatorState = { ...calculatorState, ...calculatorPreferences };
    }
  } catch (error) { /* Storage is optional; never prevent a local calculation. */ }
  document.getElementById('positionForm').addEventListener('input', (event) => {
    const field = event.target.dataset.positionField;
    if (field) saveCalculatorPreference(field, event.target.value);
  });
  document.querySelectorAll('[name="capitalPreset"]').forEach((radio) => {
    radio.addEventListener('change', () => setQuickCalculatorField('capital', Number(radio.value)));
  });
  remember.addEventListener('change', () => {
    calculatorPreferences = {};
    try {
      localStorage.setItem(calculatorPreferenceKey, JSON.stringify({ remember: remember.checked, defaultsVersion: calculatorDefaultsVersion, values: {} }));
    } catch (error) { /* No remote persistence fallback. */ }
    if (remember.checked) calculatorPreferenceFields.forEach((field) => saveCalculatorPreference(field, calculatorState[field]));
  });
  document.getElementById('resetCalculator').addEventListener('click', () => {
    calculatorPreferences = {};
    try {
      localStorage.setItem(calculatorPreferenceKey, JSON.stringify({ remember: remember.checked, defaultsVersion: calculatorDefaultsVersion, values: {} }));
    } catch (error) { /* Reset still works in memory. */ }
    document.getElementById('calcAdvanced').open = false;
  });
}

function renderSimplePositionCalculator() {
  document.getElementById('positionConfirmStatus').textContent = '';
  const result = positionApi.calculatePositionSize(calculatorState);
  document.getElementById('selectedStock').textContent = calculatorState.stockSymbol || calculatorState.stockName
    ? `${calculatorState.stockName || '股票'}（${calculatorState.stockSymbol || '未填代號'}）`
    : '股票資料（選填）';
  document.getElementById('entryRangeHint').textContent = calculatorState.originalEntryRange || '';
  document.getElementById('tradeUnit').value = calculatorState.tradeUnit;
  document.querySelectorAll('[name="capitalPreset"]').forEach((radio) => { radio.checked = Number(radio.value) === Number(calculatorState.capital); });
  document.getElementById('calcSettingsSummary').textContent = `風險 ${fmt(calculatorState.riskPercent)}% · 上限 ${fmt(calculatorState.maxPositionPercent)}% · 成本 ${fmt(calculatorState.feeBufferPercent)}%`;
  renderPositionErrors(result);
  positionFieldNames.forEach((field) => {
    const error = document.getElementById(`${field}Error`);
    const input = document.getElementById(field);
    const message = calculatorState[field] === '' ? '' : result.errors[field] || '';
    if (error) error.textContent = message;
    if (input) {
      input.setAttribute('aria-invalid', String(Boolean(message)));
      if (error) input.setAttribute('aria-describedby', error.id);
    }
  });
  if (['riskPercent', 'maxPositionPercent', 'feeBufferPercent'].some((field) => result.errors[field] && calculatorState[field] !== '')) {
    document.getElementById('calcAdvanced').open = true;
  }
  const value = (formatted) => result.valid ? formatted : '—';
  const keyResults = [
    ['試算股數', value(formatShares(result.finalShares)), value(formatLots(result.lotCount))],
    ['預估投入金額', value(formatTwd(result.estimatedInvestment)), ''],
    ['觸及停損時預估損失', value(formatTwd(result.estimatedLoss)), '含成本緩衝'],
  ];
  document.getElementById('positionResults').innerHTML = keyResults.map(([label, amount, detail]) => `<div class="calcKeyResult"><span>${label}</span><b>${amount}</b><small>${detail}</small></div>`).join('');
  const details = [
    ['風險容許金額', formatTwd(result.riskBudget)], ['每股價格風險', formatTwd(result.riskPerShare)],
    ['風險上限股數', formatShares(result.riskShares)], ['資金上限股數', formatShares(result.capitalShares)],
    ['約當張數', formatLots(result.lotCount)], ['占總資金比例', formatPercent2(result.capitalUsagePercent)],
    ['預估總資金風險比例', formatPercent2(result.riskUsagePercent)], ['成本緩衝金額', formatTwd(result.feeBuffer)],
  ];
  document.getElementById('positionBreakdown').innerHTML = details.map(([label, amount]) => `<div class="resultItem"><span>${label}</span><b>${value(amount)}</b></div>`).join('');
  const incomplete = ['capital', 'entryPrice', 'stopLossPrice'].some((field) => calculatorState[field] === '');
  document.getElementById('zeroReason').textContent = incomplete ? '待填資金與價位。'
    : Number(calculatorState.maxPositionPercent) === 0 ? '帶入部位上限為 0%，目前不開新倉。'
      : !result.valid ? Object.values(result.errors)[0] : result.finalShares === 0 ? result.zeroReason : '';
  document.getElementById('calcCostWarning').textContent = result.valid && result.estimatedLoss > result.riskBudget
    ? '含成本緩衝後的預估損失高於風險容許金額。' : '';
  document.getElementById('copyPositionResult').disabled = !result.valid;
  document.getElementById('reloadAnalysisValues').disabled = !calculatorState.selectedStock;
  return result;
}
