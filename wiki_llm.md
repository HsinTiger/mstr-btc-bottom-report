# wiki_llm 投資知識庫出口

> 這是給人與 LLM 共用的投資知識庫入口。前端瀏覽用 `wiki.html`；LLM 讀取用 `wiki/manifest.json` 與各 Markdown 頁。

## 保留原則

1. **保留結構化知識**：MSTR、BMNR、STRC、比特幣五維市場體制模型、市場價格對淨資產價值反身性、兩等份計畫、週期收益遞減。
2. **移除噪音入口**：不再為同一批四週期資料維護多個重複前端；市場總編統籌結論，細節保留在可稽核後端。
3. **前端與 LLM 分工**：
   - `market-intelligence.html`：每日跨市場結論、八個研究桌與反證條件。
   - `market-monitor.html`：每小時報價、衍生品、ETF、DAT 與輪動原始證據。
   - `wiki.html`：知識庫查詢、概念關聯、來源追溯。
   - `wiki/**`：LLM 可讀、可維護、可交叉引用的 Markdown。

## 建議導航

| 使用者問題 | 入口 |
|---|---|
| 今天市場真正由什麼力量主導？ | `market-intelligence.html` 市場總編 |
| 即時價格、槓桿、流向與輪動怎麼看？ | `market-monitor.html` 即時數據 |
| MSTR/BMNR/STRC/模型細節是什麼？ | `wiki.html` |
| 歷史策略與舊規則是什麼？ | 已封存的 `institutional-ic-memo-v3.md`、`btc-trading-system.md` |

## 當前核心知識節點

- `wiki/overview.md`：投資體系總覽。
- `wiki/entities/mstr.md`：主攻標的，現貨大倉核心。
- `wiki/entities/bmnr.md`：現貨大倉衛星，與 MSTR 比例 1:4。
- `wiki/entities/strc-preferred.md`：MSTR 資本結構與次級風險。
- `wiki/concepts/five-dimension-model.md`：每日追蹤指標框架。
- `wiki/concepts/mnav-reflexivity.md`：MSTR 市場價格對淨資產價值的折溢價與反身性風險。
- `wiki/concepts/two-tranche-plan.md`：現貨分批與週期倉規則。
- `wiki/concepts/target-price.md`：40,000–52,000 美元條件式情境假說、驗證日期與證偽條件。
- `wiki/sources/data-feeds.md`：資料源與替代源。

## 決策治理護欄

- **市場與載具分帳**：比特幣市場體制只使用比特幣資料；MSTR 與 BMNR 的估值、資本結構、賣幣、稀釋及優先求償權另列載具執行覆蓋層。
- **估值方向一致**：MSTR 普通股市值／普通股淨值倍率愈低才相對便宜；高於 1.0 倍是溢價，可能有利融資飛輪，但不是安全邊際改善。
- **來源不能冒充共識**：單一美國比特幣現貨交易所交易基金匯總來源只作輔助，不得成為硬確認票；重要結論須有第二來源或不同類型證據交叉核對。
- **目標價保持條件式**：40,000–52,000 美元不是已確認底部。引用時必須同時帶出假說狀態、最近驗證基準日與證偽條件。
- **使用完整名稱**：介面與 Wiki 一律顯示完整指標名，不使用流水號代稱；縮寫只能置於完整名稱之後作檢索輔助。

## 去蕪存菁後的公開介面

- 市場總編是唯一 BTC／ETH 分析首頁，根網址自動導向該頁。
- 即時數據只呈現可核對的市場原始證據，不再與總編爭奪結論入口。
- 四週期、原始時序與每日洞察資料仍由 collector／verifier 更新，但不再拆成重複公開頁面。
- wiki 保留長文、定義與推理；市場總編保留結論、證據與反證。

## 新增獨家指標層（2026-07-07）

- `metrics-spec.md`：普通股市值／普通股淨值、企業價值／比特幣總值、特別股稀釋污染旗標、明示固定義務覆蓋月數、每週賣幣壓力倍數、每股比特幣含量與 STRC 優先股折價的自算規格。
- `calc/mnav_calc.py`：自算市場價格對淨資產價值、覆蓋月數與每週賣幣壓力倍數工具。
- `wiki/concepts/mnav-definition-risk.md`：市場價格對淨資產價值的定義權風險。
- `wiki/concepts/delayed-pro-cyclical.md`：延遲順週期與 tranche 類比。
- `wiki/concepts/indicator-regime-change.md`：Pi Cycle 頂部與底部指標、比特幣市值對已實現價值比率等舊市場體制指標降權。
