# Market Editorial Intelligence

這個 repo 現在是一套 **資料分析與市場狀態判讀系統**，不是交易策略或自動下單系統。

核心問題只有五個：

1. 日線、週線、月線、季線各自正在走什麼趨勢？
2. 價格、跨資產廣度、衍生品、ETF、情緒與資本結構是否共振？
3. MSTR／BMNR 相對 BTC／ETH 的強弱，是否與估值及財庫結構背離？
4. 今天的觀點相較上一個相異日期改變了什麼，什麼證據會使它失效？
5. 政策、鏈上、美元流動性、信用、債券、原油與美股，是否正在跨市場共振？

## Active product

| Page | Responsibility |
|---|---|
| `market-intelligence.html` | **主要首頁**；每日市場總編統籌 BTC／ETH、ETF／DAT、政策、鏈上、技術、宏觀、信用、債券與美股八個研究桌 |
| `market-monitor.html` | 每小時現貨、衍生品、ETF、DAT 與熱門賽道原始證據 |
| `x-intelligence.html` | AI 總編；三類官方情報、本站編輯假說、反證、跨日差異與三個行動 |
| `wiki.html` | 指標、公司與假說的治理知識庫 |
| `site-overview.html` | 系統狀態、頁面責任、資料健康與生命週期治理 |

根網址 `index.html` 只負責導向市場總編，不再維護第二套重複首頁。四週期資料、歷史 revisions 與 append-only 洞察仍完整保留為公開可稽核 JSON，繼續供市場總編、verifier 與部署 readback 使用；只有三個重複 HTML 呈現層退場。

BTC／ETH 底層體制、MSTR／BMNR 上市載具、獨家指標與校準順序見 `institutional-analysis-plan-v2.md`。

## Data architecture

```text
Daily source collection
  -> independent market-input verification
  -> Yahoo + Kraken/Coinbase/Nasdaq completed-bar history
  -> cross-source history verification
  -> deterministic daily/weekly/monthly/quarterly analysis
  -> independent math, lineage, scope, and history verification
  -> macro/policy/on-chain collection + independent value verification
  -> deterministic eight-desk market editorial + hash-bound revision ledger
  -> append-only insight history
  -> page audit + backend-product binding audit
  -> desktop/mobile browser smoke + artifact hash production readback
```

主要產物：

- `data/daily/timescale_price_history.json`
- `data/daily/timescale_data_verification.json`
- `data/daily/timescale_intelligence.json`
- `data/daily/timescale_intelligence_history.json`
- `data/daily/timescale_intelligence_verification.json`
- `data/daily/market_universe.json`
- `data/daily/market_universe_verification.json`
- `data/daily/market_context.json`
- `data/daily/market_context_verification.json`
- `data/daily/market_editorial.json`
- `data/daily/market_editorial_history.json`
- `data/daily/market_editorial_verification.json`

`timescale_intelligence.json` 另外保留兩層不可混稱的時間尺度：原有日 K 等長窗口用於跨資產一致比較；`technical_horizons.weekly/monthly` 則由完整日 K 聚合成「已完成週 K／月 K」，重算 RSI 14、MACD、量價、OBV、ATR、20／30 週均線、10／20 月均線、頂底背離、領先訊號與落後確認。消息情緒只納入已驗證 ETF、政策、流動性、鏈上及情緒證據，且不具交易執行資格。

每次 `market_universe` 更新後，工作流必須依序重算並驗證 timescale → market editorial，再將上游與全部下游 artifact 原子提交；舊 verifier 即使曾通過，也不能替新上游批次背書。

Pages 的 `deployment-manifest.json` 會綁定 commit、市場總編 semantic hash，以及十個關鍵公開 artifact（五個四週期、原始觀測、每日快照、每日獨立驗證、市場總表與市場 verifier）的 SHA-256／bytes；正式站驗收同時確認三個退場頁為 404、五頁桌面／手機可讀與根網址轉址。

## 逐卡來源契約

`market-monitor.html` 的 30 張分析卡各自綁定 evidence ledger，直接顯示可點原始來源、資料觀測日、系統抓取時間、來源原生更新頻率、驗證方法、新鮮度規則與已知限制。每張卡都必須通過獨立 verifier；卡片、來源、日期或驗證來源任一缺漏時，頁面 fail closed，不沿用舊值。

「每小時更新」指市場總表每小時重建，不代表每個來源都是盤中即時。現貨、衍生品與 DAT 聚合來源按小時重抓；美國現貨 ETF 上游按交易日每日重抓，屬交易日完成後的 T+1 資料，只發布同日完成資料商、官方發行商與獨立備援三方驗證的版本；DAT 官方申報依事件更新。所有欄位保留原始觀測日與實際抓取時間，不把組裝或抓取時間冒充持倉日。

方法與維護程序位於 `skills/market-timescale-intelligence/SKILL.md`；其 evidence ledger、diff-first、deterministic gate 與 append-only revision 方法取自 `HsinTiger/skills-radar` 的已審研究原則。資料 reconciliation 方法採用已審、固定 commit 且 Apache-2.0 的 Anthropic skill，只移植對帳與治理流程，不採用通用範例門檻。

## Research boundary

- 只描述趨勢、加速度、波動率、回撤、區間位置、相對強弱、背離與共振。
- 不輸出買進、賣出、加減碼、槓桿、部位或目標價。
- 核心來源或血緣失敗時只發布 diagnostics，不沿用舊結論。
- 少於 20 個相異日期時，不宣稱經驗分位、勝率或統計顯著性。

舊策略文件保留作研究歷史，但不屬於 active runtime。
