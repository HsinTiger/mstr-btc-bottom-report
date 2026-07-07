# BTC / MSTR 機構級投資委員會備忘錄 v3.0
### 週線與月線做多策略升級版

> 日期：2026-07-07｜用途：把個人交易體系升級為可審計、可否決、可交接的 Investment Committee Memo  
> 範圍：MSTR+BMNR 現貨週期倉、MSTR 2.5x 合約波段、DAT 風險閘門  
> 結論先行：**目前不是加槓桿追多的機構級環境；主策略是等待可驗證觸發，並因 Strategy BTC monetization / reported BTC sales 事件把 MSTR 閘門從黃燈升為紅燈覆核。**

---

## 1. Executive Summary

| 問題 | 機構級裁決 |
|---|---|
| MSTR/BMNR 是否可做多？ | 可，但只能依狀態機。大倉現貨等待 S2 投降或 S3′/S4 確認；小倉只做 MSTR 2.5x 月線到年線衛星。 |
| 今天是否可開 MSTR 2.5x 合約？ | **否。** S1 仍是主判定，投降/確認條件未齊；Strategy 賣幣事件使機構閘需先紅隊覆核。 |
| 週線策略 | 小倉只做 MSTR 2.5x 月線到年線波段；紅燈未解除、BTC/MSTR 未右側確認、清算緩衝不足時禁開。 |
| 月線策略 | 大倉只做 MSTR+BMNR 現貨 4:1，抓 1–4 年週期；BMNR 是 20% ETH beta 衛星，不升主倉。 |
| 最大風險 | 把「底部研究」誤用成「高槓桿信念」。v3 把信念全部轉為觸發器、否決權、停機條件。 |
| 今日新增紅旗 | Strategy/MSTR 已宣布 Digital Credit Capital Framework 與 BTC monetization，且官方 purchases 頁已列出 2026-06-30 與 2026-07-06 的 BTC 減少紀錄；「財庫不賣幣」假設不再可視為未破壞。 |

---

## 2. 投資論點與反論點

### 2.1 Base Case：可投資，但不可預測式重倉

- BTC 仍是週期性高波動資產；趨勢右側與投降後反彈是可研究的正期望區。
- ETF、DAT、穩定幣/RWA 使 BTC 的制度化支撐比上一輪強，但也使邊際買盤更順週期。
- 本輪若沒有最後一跌，S3′「時間換空間」路徑必須接管，避免因等待完美價位而空手。

### 2.2 Bear Case：財庫公司反身性可能變成下跌放大器

- Strategy 宣布 BTC monetization 後，市場需重新評估 MSTR 不賣 BTC 的核心敘事。
- 若賣幣是為服務信用/股息/融資結構，普通股持有人承擔的不是單純 BTC beta，而是財務工程次級風險。
- 若 BTC 下跌、mNAV 折價擴大、融資成本上升同時發生，MSTR 可能從 BTC 槓桿上行工具變成賣壓與折價雙重放大器。

### 2.3 Variant View：不要等最後一跌，也不要把沒有最後一跌當牛市

少數觀點不是「現在一定漲」，而是：

1. 最後一跌是擁擠劇本，不能作為唯一進場路徑。
2. 無投降上行必須用更長時間與資金流確認，不能因為沒跌就追。
3. 合約要賺的是錯誤定價的時間窗，不是完整週期。

---

## 3. 決策矩陣

| 狀態 | 條件摘要 | 大倉現貨 4:1 | 小倉 MSTR 2.5x | MSTR 紅燈 |
|---|---|---:|---:|---:|---|
| S1 深熊下跌 | 200DMA 下、投降未齊 | 等觸發 | 禁開 | 紅燈覆核，不加 |
| S2 投降 | 價值觸發＋實現虧損＋極端情緒/費率 | 可分批接刀 | 只在 MSTR 反轉且紅燈解除後可開 | 只允許 IC 批准 |
| S3 投降後築底 | ≥14 天不創新低＋結構確認 | 可補 | 觀察，等 MSTR 右側 | 需 mNAV 與賣幣事件降級解除 |
| S3′ 無投降築底 | ≥45 天不創新低＋ETF/週線確認 | 1/3 DCA | 可準備，但不追短線 | 仍需紅燈解除 |
| S4 確認上行 | ETF/200DMA/mNAV/溫度齊 | 第 2 批 / 補到 4:1 | 可用 2.5x 做月線/年線波段 | 解除紅燈後才可恢復 |
| S5 狂熱 | 溫度過熱、情緒/槓桿過高 | 出場 | 禁開 | 降曝險 |

---

## 4. 風險登錄表

| 風險 | 等級 | 觸發 | 影響 | 控制 |
|---|---|---|---|---|
| 清算風險 | Critical | 槓桿過高、清算價貼近止損 | 看對方向仍出局 | MSTR 合約≤2.5x、現貨無清算、清算距離硬檢查 |
| MSTR 賣幣 / monetization | Critical | Strategy 實際賣 BTC 或啟動 monetization | 財庫敘事折價、mNAV 壓縮 | 機構閘紅燈；凍結 MSTR 加倉與小倉 2.5x 合約 |
| ETF 順週期流出 | High | 連 5 日淨流出、IBIT 同步流出 | 假突破、流動性抽離 | 禁新小倉合約；既有小倉先降 1/2 |
| 事件窗插針 | High | CPI/FOMC/SEC/交易所事故 | 滑點與假突破 | ±24h 禁開新倉，重大政策 ±48h |
| 模型過擬合 | High | 只靠 2–4 次歷史底部 | 假精確 | 只用觸發器，不用單一價位；20 筆後 PF<1.2 停用 |
| 行為偏誤 | High | 連虧後加槓桿、想翻本 | 策略失真 | 連 2 虧停 30 天，連 3 虧停 90 天 |
| 資料陳舊 | Medium | MVRV/mNAV/ETF 未更新 | 錯判狀態 | 標註 STALE，交易前必查 routine |

---

## 5. Portfolio Construction

### 5.1 資金分桶

| 桶 | 用途 | 上限 | 工具 |
|---|---|---:|---|
| Core | 1–4 年週期倉 | 主要資金 | MSTR/BMNR 現股 4:1（受閘門） |
| Tactical | MSTR 2.5x 小倉 | 合約虧損預算硬頂 | MSTR 合約或等效 2.5x 曝險 |
| Reserve | 生存現金 | 6–12 個月還款/生活現金 | 不進場 |
| Research | 小額觀察 | 可忽略 | 新資料源與策略測試 |

### 5.2 槓桿政策

- 借款買現股已是隱性槓桿，不得再用高槓桿把同一風險疊兩次。
- 月線策略優先用無清算工具；永續只作 30 天內戰術倉。
- 合約虧損不是用下一筆放大追回，而是由停機規則吸收。

---

## 6. Execution Playbook

### 6.1 開倉前 IC Checklist

1. 狀態機是哪一格？證據是否今天更新？
2. 這筆是大倉現貨還是小倉 2.5x？不可混用。
3. 是否有任何紅燈：MSTR monetization、ETF 流出、事件窗、資料陳舊？
4. 清算價是否遠於止損 + 2×ATR 緩衝？
5. 如果先跌 12%，是止損、縮倉還是清算？
6. Thesis Card 是否寫明失效條件與時間停損？
7. 這筆虧損後，全年合約模組是否仍可運作？

### 6.2 出場紀律

- 小倉 2.5x：+2R 出 1/3，+4R 再出 1/3，剩餘追蹤。
- 大倉現貨 4:1：不看日內噪音；依 S4/S5、mNAV、週期溫度與 1–4 年 thesis 分批調整。
- ETF 連 5 日流出且價格跌破關鍵均線：不等目標，先降曝險。
- MSTR 紅燈未解除前，不以 MSTR 反身性當作加碼理由。

---

## 7. Governance & Audit

| 頻率 | 會議 | 輸出 |
|---|---|---|
| 每日 | Routine monitor | 價格、均線、ETF、F&G、費率、mNAV、紅燈 |
| 每週六 | Red-team review | 本週交易、違規、R 分布、狀態機偏差 |
| 每月第一週 | IC review | 是否調整狀態、倉位上限、MSTR 閘門 |
| 每 20 筆合約 | Strategy review | PF、maxDD、勝率、平均 R；PF<1.2 停用 |
| 重大事件後 24h | Incident review | MSTR 賣幣、ETF 異常、交易所/監管事件 |

---

## 8. 今日 Action Items（2026-07-07）

1. **不開新 MSTR 2.5x 合約**：S1/S2/S3 條件未齊，且 MSTR 機構閘需重評。
2. **把 Strategy monetization / reported BTC sales 升為紅燈覆核**：確認 EDGAR/公司公告、2026-06-30 −1,363 BTC 與 2026-07-06 −2,225 BTC 的用途、價格、是否持續。
3. **更新 dashboard 紅燈**：MSTR 從「授權有效未動用」改為「monetization 已啟動，凍結依賴 MSTR 的加倉」。
4. **建立 IC memo 版本控管**：之後所有策略改動先進 memo，再進 dashboard。
5. **下次可行開倉只剩兩路**：大倉等 S2 投降或 S4 右側確認；小倉等 MSTR 紅燈解除＋月線/年線趨勢確認。

---

## 9. 機構級結論

這份策略的機構級版本不是更會喊點位，而是更能拒絕交易：

- 有明確投資論點，也有同等重量的反論點。
- 有紅燈與停機權，不讓單一看法凌駕風控。
- 有週線與月線分桶，不把短線合約虧損滾成長線災難。
- 有可量化的證偽條件：20 筆後 PF<1.2 或 maxDD 超過 −5%，合約 v2 退回觀察。

**因此今天的機構級動作是：升級報告、凍結衝動、等待觸發。**


---

## 10. Source Pack

| 主題 | 來源 | 用途 |
|---|---|---|
| Strategy BTC monetization | https://www.strategy.com/press/strategy-announces-digital-credit-capital-framework_06-29-2026 | 確認 Digital Credit Capital Framework、USD Reserve Policy、STRC Dividend Policy、repurchase authorizations、BTC Monetization Program |
| Strategy BTC holdings / purchases | https://www.strategy.com/purchases | 確認官方 BTC 持倉、BTC Acq 變動、2026-06-30 與 2026-07-06 兩筆 BTC 減少紀錄 |
| Strategy current metrics | https://www.strategy.com/ | 確認 mNAV、BTC reserve、USD reserve、dividend coverage、preferred/debt 指標 |
| 永續清算機制 | https://www.binance.com/en/support/faq/detail/360033525271 | 驗證標記價格、維持保證金、清算距離納入策略的必要性 |
| BTC 期貨保證金 | https://www.cmegroup.com/markets/cryptocurrencies/bitcoin/bitcoin.margins.html | 驗證專業市場對 BTC 波動與保證金的高風險定價 |
| 虛擬貨幣風險 | https://www.cftc.gov/LearnAndProtect/AdvisoriesAndArticles/understand_risks_of_virtual_currency.html | 驗證合約/期貨/虛擬貨幣高度波動與客戶風險揭露 |

`[VERIFIED]` 本 memo 的 Strategy 紅燈依據優先使用 Strategy 官方 press / purchases / metrics；媒體報導僅作輔助，不作主依據。
