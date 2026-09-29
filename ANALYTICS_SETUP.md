# 網站瀏覽統計

- GA4 評估 ID：`G-NHLL9S7CLR`。這是公開識別碼，不是密碼或 API Key。
- 僅正式網站 HTTPS 路徑啟用；localhost、file、其他網域不載入 Google 程式。
- 訪客選擇「允許統計」後才載入 Google Analytics。拒絕不影響選股、追蹤與試算。
- 頂端「瀏覽統計設定」可改變選擇；拒絕會停止本頁後續統計，不會刪除先前已送出的資料。瀏覽器的 GPC／DNT 優先於已儲存同意。
- 本功能統計訪客／工作階段、裝置與三個頁面的瀏覽，不是精確真人數。拒絕、阻擋器、Cookie 清除或不同裝置都會影響數據。
- 不新增股票、搜尋、試算、複製等事件，不讀取表單。頁面網址採固定白名單，referrer 只保留來源 origin，不傳查詢參數或任意 hash。
- 廣告儲存、廣告使用者資料及個人化同意均拒絕，停用 Google signals。

## Google Analytics 必要設定

1. 管理 → 資料串流 → 此網站串流，確認評估 ID 為 `G-NHLL9S7CLR`。
2. **關閉「加強型評估」**。本網站自行記錄三頁切換，避免歷史網址變更重複計數，以及自動收集表單／搜尋等互動。
3. 不要另外加裝同一 ID 的 Google tag 或 GTM 追蹤碼。
4. 報表時區設定為台北。
5. 正式網站開啟後選擇「允許統計」，切換三個頁籤；到 GA「報表 → 即時」檢查。正式報表並非立即完成。
6. 「網頁和畫面」可依頁面標題分辨主升段觀察、資金計算機、策略追蹤。虛擬統計路徑為 `/gpt-ai-assistant/analysis`、`calculator`、`tracking`，不是新增實體網頁。

只能從啟用後累積資料，不能補算之前的訪客。Google 端收件／報表需由管理者登入確認；程式測試使用攔截請求，不灌入正式流量。

## 維護與測試

程式為獨立的 `site-analytics.js`、`site-analytics.css`，index 只加入引用。路由透過現有頁籤 `data-page` 觀察，不修改選股或頁籤程式。

`npx playwright test tests/e2e/analytics.spec.js`：測試正式來源、同意前阻擋、三頁去重、敏感值不傳送、拒絕、儲存失敗與手機版。

官方參考：https://developers.google.com/analytics/devguides/collection/ga4/views
