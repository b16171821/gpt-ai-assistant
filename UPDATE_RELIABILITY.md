# 更新可靠性

## 台灣時間排程

- 股票更新：平日 14:40、15:40、19:10；美股收盤後週二至週六 06:40。
- Stock Update Watchdog：平日 15:17 至 22:17，每小時檢查一次。
- GitHub 排程並非準點保證，可能延遲或漏跑。Watchdog 同樣使用 GitHub，不是獨立服務 SLA。
- 訪客人數更新與股票更新是不同工作，不能用訪客更新時間代表股票資料日期。

## 日期檢查

`scripts/check_update_freshness.py` 只讀取策略輸出，不修改公式或股票資料。
以證交所 MI_5MINS_HIST 公開月報的實際收盤交易日為準，讀取本月與上月，
排除未收盤的當日與未來日期，跨月、週末、假日均不靠「週一到週五」猜測。
這是「官方已公布最新收盤日」檢查；若官方本身尚未公布新資料，不能宣稱已取得當日收盤。

台股掃描後、更新鎖定與追蹤前，以及發布前，均確認：

- officialDataDate 與 strategyAsOfDate 等於官方最新日。
- freshnessStatus 是 COMPLETE、股票清單非空、每檔日期一致。
- API 失敗或格式錯誤直接失敗，不假造日期，不發布失敗工作中的資料。

Watchdog 只在資料落後且沒有 main 股票更新正在執行或排隊時，使用內建 GITHUB_TOKEN
觸發同一個 Update Stock Data，不需要額外 Secret。僅授予 actions:write 與 contents:read；
只支援 main 上的排程與手動觸發，沒有 PR 或外部輸入執行權限。

## 故障排查

先查看 Update Stock Data，再看 Stock Update Watchdog 與 Pages 部署。
若上游 API 失敗，保留最後成功資料；工作會顯示失敗，下個檢查時段再嘗試。
Watchdog 補跑請求成功不等於股票資料更新成功，仍需確認掃描、發布與部署均成功。
需要立即更新時，可手動 Run workflow。嚴格準點需求需另設獨立排程服務，不能靠增加 cron 保證。

## 測試

```text
python -m unittest tests.test_update_freshness tests.test_strategy_regression -v
python -m unittest discover -s tests -p 'test_*.py'
python scripts/check_update_freshness.py --check-only
```

不允許把測試 baseline 或策略日期改成今天來掩蓋資料延遲。
