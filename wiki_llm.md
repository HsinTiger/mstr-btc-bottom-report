# wiki_llm 投資知識庫出口

> 這是給人與 LLM 共用的投資知識庫入口。前端瀏覽用 `wiki.html`；LLM 讀取用 `wiki/manifest.json` 與各 Markdown 頁。

## 保留原則

1. **保留結構化知識**：MSTR、BMNR、STRC、五維模型、mNAV 反身性、兩等份、週期遞減。
2. **移除噪音入口**：首頁不再塞滿全部推理，首頁只顯示每日大字卡與關鍵決策。
3. **前端與 LLM 分工**：
   - `index.html`：今天該不該動、大小倉狀態、紅黃綠燈。
   - `dashboard.html`：日/週/月監控與交易紀錄。
   - `wiki.html`：知識庫查詢、概念關聯、來源追溯。
   - `wiki/**`：LLM 可讀、可維護、可交叉引用的 Markdown。

## 建議導航

| 使用者問題 | 入口 |
|---|---|
| 今天該不該買/賣/開合約？ | `index.html` 大字卡 |
| 指標與歷史趨勢怎麼看？ | `dashboard.html` |
| MSTR/BMNR/STRC/模型細節是什麼？ | `wiki.html` |
| 策略完整規則與證偽條件？ | `institutional-ic-memo-v3.md`、`btc-trading-system.md` |

## 當前核心知識節點

- `wiki/overview.md`：投資體系總覽。
- `wiki/entities/mstr.md`：主攻標的，現貨大倉核心。
- `wiki/entities/bmnr.md`：現貨大倉衛星，與 MSTR 比例 1:4。
- `wiki/entities/strc-preferred.md`：MSTR 資本結構與次級風險。
- `wiki/concepts/five-dimension-model.md`：每日追蹤指標框架。
- `wiki/concepts/mnav-reflexivity.md`：MSTR 折溢價與反身性風險。
- `wiki/concepts/two-tranche-plan.md`：現貨分批與週期倉規則。
- `wiki/sources/data-feeds.md`：資料源與替代源。

## 下一步去蕪存菁

- 將過期的「信貸重倉」語氣降級為歷史版本，不再出現在首頁首屏。
- 首頁只保留 6–8 張大字卡：大倉、小倉、MSTR 紅燈、BTC regime、ETF 流、mNAV、200DMA/200WMA、情緒/溫度。
- wiki 保留長文與推理，首頁只保留行動訊號。
