# Strategy Guard

本文件定義不可在一般 UI、資料、效能、測試或部署任務中變更的商業策略邊界。

## 2026-11-22 核准改版：阿波五燈交易系統 1.0

核准人：楊季耘（repo owner）。本次明確核准修改掃描核心、大盤濾網、官方 K 棒合併、前端評級、鎖定／快照／追蹤，以及會更新資料的 workflow。沒有改 LINE、OpenAI、Vercel 或任何密鑰。

舊基線（A 級突破、B 級等待、C 級排除）已在同一變更中改寫。原因與選股差異如下，不是為了讓測試變綠而自動重寫。

### 已確認的舊錯誤

2026-11-20 的 `data/latest.json`：1177 檔裡 `forbiddenChase` 1056 檔、分類「禁止追高」1056 檔。其中 1052 檔的頸線距離是負數，前高距離欄位又是絕對值且大於 10。也就是跌下來的股票被當成追高。頸線距離真正大於 5% 的只有 2 檔。

### 公式與參數（新舊對照）

| 項目 | 舊 | 新 |
| --- | --- | --- |
| 系統 | 四燈＋題材燈＋ A/B/C | 阿波五燈交易系統 1.0。實作只在 `scripts/apo_five_lights.py`。前端 `derive()` 只顯示這些欄位 |
| 追高 | `abs(距 60 日高點) > 10` 或距頸線 > 5 | 只有收盤在頸線之上超過 5%，或在前高之上超過 10%，才是「延伸在壓力之上」。跌破另標「跌破確認價」 |
| 均量 | `rolling(20).mean()` 含當日 | `shift(1).rolling(20)`，前 20 個已完成交易日。歷史突破檢查同樣不含當日 |
| 美股頸線 | `High.tail(20).max()` 含今日 | 與台股相同，用前 20 日高點 |
| 美股回踩 | 只要貼著頸線 | 必須 10 個交易日內有確認突破 |
| ADX | 14 日簡單平均；+DM/-DM 先把較小的歸零，打平時 -DM 會留下 | Wilder 平滑。上漲幅度等於下跌幅度時 +DM 與 -DM 都是 0。輸出 ADX、+DI、-DI。門檻仍是 ADX ≥ 25 且 +DI > -DI |
| 價格 | 指標用未還原 Close | 指標與 60 日報酬用還原價。進場、停損、目標、頸線、官方收盤用原始價 |
| 官方 K | 寫入官方日，但不刪掉較晚的 yfinance K | 先截到 `officialDate`，再算指標。官方那根的 Adj Close 留空，沿用前一日還原係數 |
| MA240 | 不足時 `ma240 == 0` 會讓趨勢自動過 | 歷史不到 240 根，L1 是「未核實」，不算通過 |
| 過度延伸 | 無 | 收盤高於 MA20 超過 15%，或高於 MA60 超過 30%，L1 為「等待」 |
| 成交量燈 | 當日量 / 含當日均量 ≥ 2，題材也算一燈 | 突破日量 ≥ 2 倍「前 20 日」均量，或 10 日內的縮量回測（量比 0.4～1.2）。題材只是標籤 |
| 型態 | 箱型／三角／旗型名稱不要求幾何成立 | 只允許「收盤突破前20日高點」或「突破後縮量回測支撐」。要寫出確認價、失效價（頸線下 2%）與量比。高量長黑（實體 ≥ 4% 且量 ≥ 1.2 倍）直接失敗 |
| 風險燈 | 頸線下 3% 當停損，目標用測量漲幅 | 停損在 -4% 與結構失效價之間取合理者（距離 1.5%～8% 優先結構）。停損必須低於現價。目標參考 +15%。扣成本後風報比 ≥ 2。現價不在進場區就不是正式訊號 |
| 成本 | 計算機另加 3% 緩衝 | 手續費 0.1425% 單邊，折扣倍數預設 1（牌告，可改），賣出證交稅 0.3%。部位 = (資金 × 1%) / 含成本的每股風險，再依張數與流動性向下取整 |
| 波動註記 | 無 | ATR% ≥ 3% 標「4% 偏緊」；ATR% ≤ 0.8% 標「4% 偏寬」 |
| 60 日漲幅 ≥ 30% | 會影響分數 | 只標「強勢股候選」 |
| 評級 | A/B/C、勝率高/中/低 | 5 燈待執行名單、4 燈高優先觀察、3 燈觀察、0～2 不符。全燈都無法計算才是整體未核實 |
| 追蹤出場 | 目標看高價、停損看收盤 | 目標與停損都看收盤。收盤獲利達進場價 +10% 後，有效停損上移到該收盤的 96%，只升不降；當日仍用上移前的停損 |
| 大盤 | 進攻／觀察／防守／停止進場 | 保留。非進攻盤會把待執行名單降級，不會把沒過的燈改成通過 |
| 宇宙 | `MAX_CODES = 1200`，依 dict 順序截斷 | 全部 twstock 四碼普通股，依代號排序，不截斷。台股歷史改抓 3 年，讓 MA240 有機會算得出來 |
| history.csv | 每次整檔覆蓋 | 依日期＋代號追加並去重。失敗時不刪歷史 |
| 排程 | `update-data.yml` 與 `update-four-lights.yml` 都在台北 15:40 跑台股 | 只留 `update-data.yml` 發布。後者不再掃描、也不 push |
| 勝率 | 報告與 `winRate` 輸出高/中/低 | 移除。網站固定寫「尚未回測」。回測腳本是 `scripts/backtest_five_lights.py` |

凍結測試 `tests/fixtures/strategy-sample.json` 的三筆預期值已改成上面這套規則的輸出：9001 因只有 120 根 K、MA240 未核實，由 A 級改為高優先觀察（4 燈）；9002 不再因為題材或缺量能的絕對距離而給 B 級；9003 維持不符，位置是跌破確認價。

回測指令（不會把結果寫進網站）：

```bash
python scripts/backtest_five_lights.py --symbols 2330.TW,2317.TW,2454.TW --period 2y --output /tmp/five-lights-sample.json
```

樣本若來自目前仍上市的股票，有倖存者偏差，不能當成公開勝率。

## PROTECTED STRATEGY LOGIC

## PROTECTED STRATEGY LOGIC

未取得使用者針對「檔案、函式、公式或參數」的明確同意前，禁止修改、搬移、重寫、重新命名、重新加權或替換下列內容。

### 五燈核心

檔案：`scripts/apo_five_lights.py`

- 上面 2026-11-22 表列的門檻、Wilder DI、五燈判定、成本與部位公式。
- `analyze_ohlcv()`、`wilder_dmi()`、`position_size()`、`net_reward_risk()`、`append_history()`、`gate_rating()`。

`apo-five-lights.js` 只重算同一套部位公式，不得另做評級。

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
