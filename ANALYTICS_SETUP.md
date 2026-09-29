# 網站瀏覽統計

## 自動統計

- GA4 評估 ID：`G-NHLL9S7CLR`，是公開識別碼，不是 API 金鑰。
- 正式網站進站後自動統計，不跳確認框。頂部「統計設定」可拒絕；先前已拒絕或瀏覽器要求 GPC／DNT 的訪客，不會被重新啟用。
- 不新增股票、搜尋、試算、複製等事件，不讀取表單。頁面路徑僅三頁白名單，來源僅 origin，不傳網址查詢參數。
- 關閉廣告同意與 Google signals；本機、其他網域不載入 GA。
- 管理者應確認服務地區及受眾所需的隱私告知與同意要求；此技術設定不是合規認證。
- **GA 後台的「加強型評估」必須關閉**，避免歷史網址切換重複計數或自動收集互動。不再安裝第二份相同 ID 的 tag。

## 累積訪客

- 前端讀取公開的 `data/site_visitors.json`，只顯示 GA 報表總數，沒有本機自加計數器。
- 採 `totalUsers`，一次查詢 2026-09-29 至當日整個期間，限定本站 hostname 和 path，不把每天或每頁人數相加。
- GA 去重識別不等於精確真人數，跨裝置、Cookie 清除、拒絕／阻擋與處理延遲均會影響數字；UI 顯示「約」。
- 每小時第 47 分排程嘗試更新，GitHub 排程及 GA 資料處理可能延遲；不是即時在線人數。
- 尚未設定顯示「待設定」，取得失敗顯示「暫無資料」，上次成功資料超過24小時標示「待更新」。不得假造或用0代表讀取失敗。
- 前端從公開 GitHub raw 的固定 JSON 位置讀取，避免 GITHUB_TOKEN 的資料提交不觸發 Pages build 而顯示舊計數。只有彙總數與更新日期公開，無個別訪客資料。
- API失敗保留上次成功JSON；有門檻限制／抽樣的報表不更新數字，不公開錯誤回應或憑證。

## 管理者一次性設定（目前尚未完成）

1. 在 Google Analytics「管理 → 資源詳細資料」取得**純數字資源 ID**，不是 `G-` 評估 ID；資源報表時區設台北。
2. 在自己的 Google Cloud 專案啟用 **Google Analytics Data API**，建立專用服務帳戶；不需給它 Cloud 專案管理者權限。
3. GA 資源存取權管理中，加入該服務帳戶電子郵件，僅給「檢視者」。
4. 服務帳戶 JSON 金鑰只由管理者放進 GitHub repository → Settings → Secrets and variables → Actions → Secrets，名稱 `GA4_SERVICE_ACCOUNT_JSON`。**不可貼到聊天、commit、網頁、公開資料檔或 workflow log**。妥善管理並輪換金鑰；未來可改用 WIF/OIDC 取代長效金鑰。
5. 同頁 Variables 新增 `GA4_PROPERTY_ID`，值填純數字資源 ID。
6. Actions 手動執行 **Update Visitor Statistics**。未設定 ID 或金鑰時會跳過，不產生假數字。
7. 確認成功後，檢查 `data/site_visitors.json` 為 `status: OK`，與 GA 同期間／同網站的「使用者總數」核對，再重整網站。

此處不會自動建立 Cloud 專案、不購買服務、不取得管理員權限；Google 權限與私密金鑰必須由管理者設定。

## 驗證

`python -m unittest tests.test_site_visitors -v`

`npx playwright test tests/e2e/analytics.spec.js`

瀏覽器測試攔截 Google 請求，不送假流量。正式 GA 收件由管理者在「報表 → 即時」確認；歷史訪客無法回補。

官方文件：
- https://developers.google.com/analytics/devguides/collection/ga4/views
- https://developers.google.com/analytics/devguides/reporting/data/v1/quickstart
- https://developers.google.com/analytics/devguides/reporting/data/v1/rest/v1beta/properties/runReport
