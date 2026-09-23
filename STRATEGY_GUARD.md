# Strategy Guard

本文件定義不可在一般 UI、資料、效能、測試或部署任務中變更的商業策略邊界。

## PROTECTED STRATEGY LOGIC

未取得使用者針對「檔案、函式、公式或參數」的明確同意前，禁止修改、搬移、重寫、重新命名、重新加權或替換下列內容。

### 台股掃描器

檔案：`scripts/update_tw_stock_scanner.py`

- 策略常數：`MIN_RISE_60`、`MIN_VOLUME_RATIO`、`MIN_ADX`、`MIN_HISTORY_ROWS`、`MIN_TURNOVER`、`MIN_AVG_TURNOVER20`、`MAX_VOLATILITY20_PCT`、`MAX_SAFE_STOP_PCT`、`MAX_WATCH_STOP_PCT`、`MIN_SAFE_RR`、`MIN_WATCH_RR`。
- 完整收盤判斷：`trim_to_completed_daily_bars()`。
- 指標與 K 棒：`calc_adx()`、`kline_label()`。
- 型態、頸線、突破、回踩、觀察區、停損、目標與風報比：`pattern_plan()`。
- 安全分、A/B/C 分級與分類：`safety_profile()`。
- 主升段條件、均線、60 日漲幅、成交量與最終訊號：`analyze_one()`。
- 全市場選股與排序 tuple：`scan_market()` 內的 `grade_rank` 及 `results = sorted(...)`。
- 市場濾網套用及輸出欄位：`build_market_filter()`、`write_outputs()` 中與選股結果相關的內容。

### 美股掃描器

檔案：`scripts/update_us_stock_scanner.py`

- `MIN_RISE_60`、`MIN_VOLUME_RATIO`、`MIN_ADX`、`MIN_HISTORY_ROWS` 及其他策略門檻。
- `trim_to_completed_daily_bars()`、`calc_adx()`、`kline_label()`、`pattern_plan()`、`analyze_one()`。
- 嚴格候選、觀察候選、排序、突破、回踩、停損及目標邏輯。

### 大盤濾網

檔案：`scripts/market_filter.py`

- 市場狀態條件及門檻。
- `classify_market_regime()`、`apply_breadth_guard()`、`apply_market_filter_to_rows()`。
- 原始訊號、調整後訊號、持有者與空手者動作的策略判斷。

### 前端策略派生與排名

檔案：`index.html`

- `plan()`、`calcRisk()`、`exclusionReasons()`、`currentStage()`、`buyPointType()`。
- `derive()`、`refreshDecision()`、`scoreKey()`、`normalize()`。
- `isTradeCandidate()`、`isObservationCandidate()`、`mainCandidates()`。
- 任何會改變等級、訊號、分類、排名、觀察區、停損、目標或顯示候選名單的條件。

`tradableCandidates()` 與處置風險層屬外層交易風險過濾；可以維護，但不得反向修改上述技術分數與原始排名。

### 原始策略鎖定與追蹤

- `scripts/update_strategy_locks.py`：原始買入區、原始停損、原始目標及鎖定規則。
- `scripts/update_strategy_snapshots.py`：策略輸出欄位轉存及分類對應。
- `scripts/update_strategy_tracking.py`：`originalStrategy` 不覆蓋規則、追蹤狀態、達標／停損生命週期與終局狀態優先順序。

### 固定輸出契約

以下欄位均視為策略輸出：

`code`、`grade`、`safeScore`、`score`、`strictOk`、`stage`、`category`、`status`、`neckline`、`observationEntry`、`buyLow`、`buyHigh`、`stopLoss`、`target`、`riskPct`、`rewardPct`、`riskRewardRatio`、`distanceFromNecklinePct`、`action`、`reason` 以及排序順序。

## Required Workflow

1. 修改前執行：`git diff -- scripts/update_tw_stock_scanner.py scripts/update_us_stock_scanner.py scripts/market_filter.py index.html scripts/update_strategy_locks.py scripts/update_strategy_snapshots.py scripts/update_strategy_tracking.py`。
2. 先執行：`python -m unittest tests.test_strategy_regression -v`。
3. 僅修改策略外層檔案。
4. 修改後再次執行回歸測試與完整測試。
5. 再次檢查受保護路徑 diff；若出現未經授權的差異，停止工作並回報。

## Frozen Regression Baseline

- 固定輸入：`tests/fixtures/strategy-sample.json`。
- 測試：`tests/test_strategy_regression.py`。
- Baseline 固定三種輸入：A 級突破、B 級等待、C 級排除。
- 測試直接呼叫現有 `update_tw_stock_scanner.scan_market()` 路徑，並比對選股順序及所有固定輸出欄位。
- 差異時測試必須失敗，顯示 `Expected` 與 `Actual`。
- 禁止腳本自動重寫 `expected`，也禁止為了讓 CI 通過而接受新 baseline。

只有使用者明確批准策略改版後，才可人工更新 baseline，並在同一個 commit 中記錄變更原因、受影響欄位及預期選股差異。

## Allowed Without Strategy Approval

- CSS、純顯示排版、無障礙、響應式及圖表 renderer。
- 不改變輸出內容的資料 adapter、cache、錯誤處理、logging 與 API 重試。
- 資料品質檢查、測試、CI、安全掃描、文件與部署維護。
- 只讀取既有策略欄位的視覺標記。

如果無法證明修改不會影響策略輸出，視為碰到策略核心，先詢問使用者。
