# daily-data-pipeline.md — 每日資料管線與驗證代理

## 目的

每天自動更新 MSTR/BTC/BMNR/STRC 決策資料；另以每小時管線更新 BTC／ETH 衍生品、ETF、DAT 與熱門資產／賽道。所有資料先抓取、再驗證，最後才生成前端 JSON。

## GitHub Actions

- Workflow：`.github/workflows/daily-data.yml`
- 時間：每日 UTC 00:05（台北 08:05）與手動 `workflow_dispatch`
- 權限：`contents: write`，只 commit `data/daily/*.json`
- 跨資產 Workflow：`.github/workflows/market-universe.yml`，每小時第 17 分更新。

## 腳本分工

1. `scripts/daily_data_pipeline.py`
   - 抓 BTC/ETH：CoinGecko + Coinbase，價差由 verifier 交叉檢查。
   - 抓 BTC 日/週線、50/200DMA、200WMA、MVRV、Fear & Greed、ETF flow，產生五維 BTC 標準分；模型明示為未回測 heuristic。
   - 抓 MSTR/BMNR/STRC：Yahoo Finance chart API。
   - 抓 Strategy 官方 purchases ledger 與連續兩期 SEC 8-K；只在完整揭露週可由持幣對帳證明零賣幣，否則保留未知。這是「最新完整揭露週」，不是假裝知道每個日曆日。
   - 抓 MSTR SEC companyfacts、最新 10-Q/10-K 資本結構表、後續每週 8-K ATM ledger 與 Strategy point-in-time basic shares；自動重算債務面額、年票息、五檔優先股清算面額與最新 STRC 利率。BMNR 仍只產生 gross treasury view，不冒充 net NAV。
   - 有揭露基準日的結構與鏈上資料寫入 `as_of`；所有 observation 均保留 `basis`、`source_tier` 與 `fetched_at`。即時/收盤報價若來源未提供結構日期，不偽造 `as_of`。
   - 產出 `data/daily/raw_observations.json`、`latest_snapshot.json`、`database.json`。

2. `scripts/collect_market_universe.py`
   - 現貨：BTC、ETH、HYPE、SOL、BNB、XRP、DOGE，採 CoinGecko、Coinbase、OKX 與可用的 Binance 交叉；USDT 先換算 USD，HYPE 永續標記價不計入現貨來源。
   - 永續：BTC／ETH 的 Bybit、OKX、Hyperliquid 與可用 Binance 資金費率、未平倉量與成交額；先依各場域週期年化再比較。
   - 期貨：優先 Deribit 離 90 日最近掛牌月到期合約；runner 無法存取時改用 OKX 離 90 日最近的幣本位到期合約，另列 CME 前月 Yahoo 代理。
   - 期權：優先 Deribit 幣本位 DVOL、Put／Call、ATM IV、最大痛點與 OI；runner 無法存取時改用 OKX 幣本位 Put／Call 與近 30 日 ATM 標記 IV。DVOL 與 OKX ATM IV 不串成同一序列。
   - 機構流：BTC ETF、ETH ETF 可用性、BTC／ETH DAT 公司持倉。
   - 賽道：RWA、Layer 1、DeFi、Meme 採版本化固定五資產籃子，由 CoinGecko、CoinPaprika、Binance 同成分重算 24 小時報酬；至少雙來源且差異不高於 1 個百分點。
   - BTC 長期論證：黃金貨幣化比例、穩定幣與 RWA 規模、公開公司持幣滲透／集中度、算力安全及美國主權信用競爭。
   - 長期論證另用 `structural_context_quality` 驗證；不得污染短線執行品質與交易閘門。
   - 產出 `market_universe.json` 與 `market_universe_history.json`。

3. `scripts/verify_daily_data.py`
   - 這是資料驗證 sub-agent 的可執行版本。
   - 重讀 raw 與 snapshot，不信任 collector 結論。
   - 檢查必要來源、BTC/ETH 主備價差、來源時效、衍生指標合理範圍，並獨立重算 SEC 資本結構、ETF、DAT 與固定賽道籃子。
   - 驗證 BTC regime 不得混入 MSTR/BMNR 載具風險；ETF 未通過三來源契約時不得進入分數。
   - 驗證 BMNR bottom-up gross treasury 與公司 reported total 的差距。
   - 產出 `data/daily/agent_verification_report.json`。

4. `scripts/generate_daily_extensions.py`
   - 結合 `wiki_llm.md` 的概念層與已驗證 snapshot。
   - 產出今天三個延伸觀點。
   - 前端顯示昨天、今天、明天觀察；過期項目移入 `archive`。
   - archive 依 `date + type` 去重，避免每日執行造成重複累加。

5. `scripts/generate_institutional_analytics.py`
   - 產生動態投委會結論，不使用固定一句話。
   - 輸出資料品質 0–100、BTC/MSTR/BMNR 三本帳、MSTR BTC 情境壓測與風險堆疊。

## 前端資料

- `daily-extensions.html`：每日三觀點、昨天/今天/明天對比、wiki study inputs。
- `dashboard.html`：新增 daily data JSON 與驗證代理報告入口。
- `index.html`：新增每日延伸入口。
- `market-monitor.html`：跨資產現貨、衍生品、ETF、DAT 與賽道輪動。

## 驗證政策

- BTC/ETH 必須由 CoinGecko、Coinbase、Kraken 來源池至少兩家通過，價差門檻 1.5%。
- MSTR 以 Yahoo regular-market close 為基準，Nasdaq 作備援與新鮮度證據。
- BMNR 價格或官方 treasury 資料缺漏時降信心；BMNR gross assets 不可宣稱為普通股淨 NAV。
- Coin Metrics MVRV 超過 3 日降級、超過 7 日 fail；Strategy 持倉超過 14 日降級；最新完整揭露週賣幣資料超過 8 日降級、14 日 fail。
- USD Reserve 超過 30 日降級、120 日 fail；普通股採 Strategy point-in-time basic shares，超過 45 日降級、120 日 fail。
- SEC/公司公告屬結構性輸入；季度基準、後續 8-K 期間連續性與官方聚合總額任一對不上即 fail-closed，不回退到人工佔位值。
- verifier fail 時產生診斷但 workflow 不發布新交易 artifacts；前端保留最後已驗證版本並明示執行狀態，不得把 failure artifact 冒充正常資料。
- 跨資產快照超過 3 小時、必要價格缺失或雙來源價差超過 2% 時 fail。BTC／ETH ETF 必須通過完整 roster、官方發行商與同日備援三來源契約。
- collector 的程序成功與資料品質分開：即使資料品質為 fail 仍寫入並提交 failure artifact，讓前端立即清空舊數字；品質欄位才是交易與顯示閘門。

## 注意

這套管線負責「資料品質與展示」，不是自動下單系統。重大資本結構事件由 SEC parser 自動納入並由 verifier 重算；未知的新表格型態、期間缺口或官方總額差異會封鎖結論，仍需人工審查 parser 契約，而不是手填數字繞過。
