# daily-data-pipeline.md — 每日資料管線與驗證代理

## 目的

每天自動更新 MSTR/BTC/BMNR/STRC 決策資料；另以四小時管線更新 BTC／ETH 衍生品、ETF、DAT 與熱門資產／賽道。所有資料先抓取、再驗證，最後才生成前端 JSON。

## GitHub Actions

- Workflow：`.github/workflows/daily-data.yml`
- 時間：每日 UTC 00:05（台北 08:05）與手動 `workflow_dispatch`
- 權限：`contents: write`，只 commit `data/daily/*.json`
- 跨資產 Workflow：`.github/workflows/market-universe.yml`，每四小時第 17 分更新。

## 腳本分工

1. `scripts/daily_data_pipeline.py`
   - 抓 BTC/ETH：CoinGecko + Coinbase，價差由 verifier 交叉檢查。
   - 抓 BTC 日/週線、50/200DMA、200WMA、MVRV、Fear & Greed、ETF flow，產生五維 BTC 標準分；模型明示為未回測 heuristic。
   - 抓 MSTR/BMNR/STRC：Yahoo Finance chart API。
   - 抓 Strategy 官方 purchases ledger；若最新揭露無法覆蓋最近 7 日，賣幣壓力記為「未知」而不是 0，MSTR 合約 fail-closed。
   - 抓 MSTR SEC companyfacts、最新 10-Q/10-K 封面各類普通股實際流通股數，以及 BMNR 最新 8-K Exhibit 99.1；BMNR 只產生 gross treasury view，不冒充 net NAV。
   - 有揭露基準日的結構與鏈上資料寫入 `as_of`；所有 observation 均保留 `basis`、`source_tier` 與 `fetched_at`。即時/收盤報價若來源未提供結構日期，不偽造 `as_of`。
   - 產出 `data/daily/raw_observations.json`、`latest_snapshot.json`、`database.json`。

2. `scripts/collect_market_universe.py`
   - 現貨：BTC、ETH、HYPE、SOL、BNB、XRP、DOGE，採 CoinGecko、Coinbase、OKX 與可用的 Binance 交叉；USDT 先換算 USD，HYPE 永續標記價不計入現貨來源。
   - 永續：BTC／ETH 的 Bybit、OKX、Hyperliquid 與可用 Binance 資金費率、未平倉量與成交額；先依各場域週期年化再比較。
   - 期貨：優先 Deribit 離 90 日最近掛牌月到期合約；runner 無法存取時改用 OKX 離 90 日最近的幣本位到期合約，另列 CME 前月 Yahoo 代理。
   - 期權：優先 Deribit 幣本位 DVOL、Put／Call、ATM IV、最大痛點與 OI；runner 無法存取時改用 OKX 幣本位 Put／Call 與近 30 日 ATM 標記 IV。DVOL 與 OKX ATM IV 不串成同一序列。
   - 機構流：BTC ETF、ETH ETF 可用性、BTC／ETH DAT 公司持倉。
   - 賽道：RWA、Layer 1、DeFi、Meme 市值與 24 小時變化。
   - BTC 長期論證：黃金貨幣化比例、穩定幣與 RWA 規模、公開公司持幣滲透／集中度、算力安全及美國主權信用競爭。
   - 長期論證另用 `structural_context_quality` 驗證；不得污染短線執行品質與交易閘門。
   - 產出 `market_universe.json` 與 `market_universe_history.json`。

3. `scripts/verify_daily_data.py`
   - 這是資料驗證 sub-agent 的可執行版本。
   - 重讀 raw 與 snapshot，不信任 collector 結論。
   - 檢查必要來源、BTC/ETH 主備價差、來源時效、衍生指標合理範圍、SEC 手動覆核警示。
   - 驗證 BTC regime 不得混入 MSTR/BMNR 載具風險；ETF 單源權重不得高於 0.5。
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

- BTC/ETH 必須同時有 CoinGecko 與 Coinbase，價差門檻 1.5%。
- MSTR 必須有 Yahoo Finance 價格。
- BMNR 價格或官方 treasury 資料缺漏時降信心；BMNR gross assets 不可宣稱為普通股淨 NAV。
- Coin Metrics MVRV 超過 3 日降級、超過 7 日 fail；Strategy 持倉超過 14 日降級；7 日賣幣揭露超過 7 日即 fail-closed。
- USD Reserve 超過 30 日降級、120 日 fail；普通股流通股數超過 45 日降級、120 日 fail。
- SEC/公司公告屬結構性輸入，API 不穩時不自動改 capital structure；保留警示並要求人工覆核。
- verifier fail 時仍完成分析並 commit 最新資料與 FAIL 報告，讓前端顯示真實失效原因；所有交易 gate 必須 fail-closed，不得沿用舊綠燈。
- 跨資產快照超過 8 小時、必要價格缺失或雙來源價差超過 2% 時 fail；ETH ETF 無穩定交叉來源時維持 degraded／未知。
- collector 的程序成功與資料品質分開：即使資料品質為 fail 仍寫入並提交 failure artifact，讓前端立即清空舊數字；品質欄位才是交易與顯示閘門。

## 注意

這套管線負責「資料品質與展示」，不是自動下單系統。重大資本結構改變、Strategy 發債/優先股/BTC sale 等事件，仍需人工從 SEC/公司公告覆核後更新 `metrics-spec.md` 與 pipeline manual inputs。
