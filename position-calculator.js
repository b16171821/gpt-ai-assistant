(function initializePositionCalculator(root) {
  const DEFAULT_STATE = {
    currentPage: 0,
    selectedStock: null,
    stockSymbol: '',
    stockName: '',
    capital: '',
    riskPercent: 1,
    entryPrice: '',
    stopLossPrice: '',
    maxPositionPercent: 20,
    tradeUnit: 1,
    feeRatePercent: 0.1425,
    feeDiscount: 1,
    sellTaxPercent: 0.3,
    originalEntryRange: '',
    isUserModified: false,
    lastAnalysisDefaults: null,
  };

  const numberFields = [
    'capital',
    'riskPercent',
    'entryPrice',
    'stopLossPrice',
    'maxPositionPercent',
    'feeRatePercent',
    'feeDiscount',
    'sellTaxPercent',
  ];

  function toFiniteNumber(value) {
    const parsed = typeof value === 'number' ? value : Number(String(value ?? '').replace(/,/g, ''));
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function safeFloor(value) {
    return Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
  }

  function calculatePositionSize(input = {}) {
    const values = {
      capital: toFiniteNumber(input.capital),
      riskPercent: toFiniteNumber(input.riskPercent),
      entryPrice: toFiniteNumber(input.entryPrice),
      stopLossPrice: toFiniteNumber(input.stopLossPrice),
      maxPositionPercent: toFiniteNumber(input.maxPositionPercent),
      tradeUnit: Number(input.tradeUnit) === 1 ? 1 : 1000,
      feeRatePercent: toFiniteNumber(input.feeRatePercent),
      feeDiscount: toFiniteNumber(input.feeDiscount),
      sellTaxPercent: toFiniteNumber(input.sellTaxPercent),
    };
    const errors = {};

    numberFields.forEach((field) => {
      const raw = input[field];
      const parsed = Number(String(raw ?? '').replace(/,/g, ''));
      if (raw !== '' && raw !== null && raw !== undefined && (!Number.isFinite(parsed) || parsed < 0)) {
        errors[field] = '請輸入大於或等於 0 的有效數字';
      }
    });

    if (values.capital <= 0 && !errors.capital) {
      errors.capital = '投資總資金必須大於 0';
    }
    if (values.riskPercent <= 0 && !errors.riskPercent) {
      errors.riskPercent = '單筆風險比例必須大於 0';
    }
    if (values.entryPrice <= 0 && !errors.entryPrice) {
      errors.entryPrice = '預計進場價必須大於 0';
    }
    if (values.stopLossPrice <= 0 && !errors.stopLossPrice) {
      errors.stopLossPrice = '停損價必須大於 0';
    }
    if (values.maxPositionPercent <= 0 && !errors.maxPositionPercent) {
      errors.maxPositionPercent = '單一標的資金上限必須大於 0';
    }
    if (
      values.entryPrice > 0
      && values.stopLossPrice > 0
      && values.stopLossPrice >= values.entryPrice
    ) {
      errors.stopLossPrice = '做多試算的停損價必須低於進場價';
    }

    const core = root.ApoFiveLights;
    const sized = core.positionSize({
      capital: values.capital,
      entry: values.entryPrice,
      stop: values.stopLossPrice,
      lotSize: values.tradeUnit,
      feeRate: values.feeRatePercent / 100,
      feeDiscount: values.feeDiscount,
      sellTaxRate: values.sellTaxPercent / 100,
      riskFraction: values.riskPercent / 100,
      maxPositionPct: values.maxPositionPercent / 100,
    });
    const riskBudget = sized.riskBudget;
    const riskPerShare = sized.riskPerShare || 0;
    const priceRisk = Math.max(0, values.entryPrice - values.stopLossPrice);
    const riskShares = riskPerShare > 0 ? safeFloor(riskBudget / riskPerShare) : 0;
    const positionBudget = Math.max(0, values.capital * (values.maxPositionPercent / 100));
    const buyCash = values.entryPrice > 0 ? values.entryPrice * (1 + (values.feeRatePercent / 100) * values.feeDiscount) : 0;
    const capitalShares = buyCash > 0 ? safeFloor(positionBudget / buyCash) : 0;
    const rawShares = sized.sharesBeforeLot;
    const finalShares = sized.shares;
    const estimatedInvestment = Math.max(0, finalShares * values.entryPrice);
    const baseEstimatedLoss = Math.max(0, finalShares * priceRisk);
    const feeCash = Math.max(0, finalShares * riskPerShare - baseEstimatedLoss);
    const estimatedLoss = Math.max(0, finalShares * riskPerShare);
    const capitalUsagePercent = values.capital > 0
      ? Math.max(0, (estimatedInvestment / values.capital) * 100)
      : 0;
    const riskUsagePercent = values.capital > 0
      ? Math.max(0, (estimatedLoss / values.capital) * 100)
      : 0;
    const lotCount = Math.max(0, finalShares / 1000);

    let zeroReason = '';
    if (finalShares === 0) {
      if (Object.keys(errors).length) {
        zeroReason = Object.values(errors)[0];
      } else if (riskBudget <= 0 || riskShares <= 0) {
        zeroReason = '風險額度不足，試算股數為 0 股';
      } else if (positionBudget < values.entryPrice || capitalShares <= 0) {
        zeroReason = '資金不足，試算股數為 0 股';
      } else if (values.tradeUnit === 1000 && rawShares < 1000) {
        zeroReason = '依輸入條件計算後不足 1000 股，整張模式試算為 0 股';
      } else {
        zeroReason = '依輸入條件計算後，試算股數為 0 股';
      }
    }

    return {
      valid: Object.keys(errors).length === 0,
      errors,
      zeroReason,
      riskBudget,
      riskPerShare,
      riskShares,
      capitalShares,
      rawShares,
      finalShares,
      lotCount,
      positionBudget,
      estimatedInvestment,
      baseEstimatedLoss,
      feeCash,
      tradingCost: feeCash,
      estimatedLoss,
      capitalUsagePercent,
      riskUsagePercent,
    };
  }

  function createPositionCalculatorState(overrides = {}) {
    return { ...DEFAULT_STATE, ...overrides };
  }

  function applyAnalysisDefaults(state, defaults, force = false) {
    const current = createPositionCalculatorState(state);
    if (current.isUserModified && !force) {
      return { ...current };
    }
    const nextDefaults = { ...(defaults || {}) };
    return {
      ...current,
      selectedStock: nextDefaults.selectedStock || null,
      stockSymbol: nextDefaults.stockSymbol || '',
      stockName: nextDefaults.stockName || '',
      entryPrice: nextDefaults.entryPrice ?? '',
      stopLossPrice: nextDefaults.stopLossPrice ?? '',
      originalEntryRange: nextDefaults.originalEntryRange || '',
      lastAnalysisDefaults: nextDefaults,
      isUserModified: false,
    };
  }

  function updatePositionStateField(state, field, value) {
    return {
      ...createPositionCalculatorState(state),
      [field]: value,
      isUserModified: true,
    };
  }

  const api = {
    DEFAULT_STATE,
    calculatePositionSize,
    createPositionCalculatorState,
    applyAnalysisDefaults,
    updatePositionStateField,
  };

  root.PositionCalculator = api;
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
  }
}(typeof globalThis !== 'undefined' ? globalThis : this));

