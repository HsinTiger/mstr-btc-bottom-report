# 2026-08-19：管線復活 + 作者論點追蹤

取代 `HANDOFF_ETF_ENDPOINTS.md`（該文件的診斷是錯的，已刪除）。

## 一句話

管線從 08-11 停擺，**不是 ETF 來源掛掉**，而是三個各自獨立的上游退化疊在一起；
守門機制自始至終都是對的，三個都往上游修，沒有放寬任何新鮮度契約。

## 前一份交接文件錯在哪

它斷定「美國現貨 BTC/ETH ETF 流量來源大批失效」。2026-08-19 逐一實測，
`collect_theblock_etf_source`、`collect_blockworks_etf_source`、
`collect_coinmarketcap_etf_source`、`collect_bitbo_btc_etf_source`、
`collect_walletpilot_etf_source` **全部正常回資料**，as_of 都在 08-18。
CI 上 ETF 步驟也是通過的。真正讓 `daily-data.yml` exit 1 的是後面的步驟。

教訓：跑一次真的 workflow 看它停在哪一步，比從症狀反推來源清單可靠。

## 真正的三個原因（依序被打開）

### 1. `verify_market_context.py` — BTC 鏈上三指標撞破 3 日新鮮度

`api.blockchain.info/charts/*` 已固定落後 3 天以上，
`transactions`／`active_addresses`／`hashrate` 每天都超過 3 日契約 → 每日管線 exit 1。

**修法**：接 CoinMetrics community API（免金鑰、T-1），
在觀測日較新時自動接手 canonical，blockchain.info 退為獨立對帳來源。
`canonical_onchain_provider` 與 `canonical_promoted_series` 會記錄這次用了誰，
verifier 會檢查「換掉的來源真的比較新」。

順手帶進 MVRV、已實現市值／價格、交易所進出金額、新增發行量，
並保留 CoinMetrics 的 `flash`（初步值）標記。

### 2. `verify_timescale_data.py` — MSTR/BMNR 拿不到第二個獨立序列

`api.nasdaq.com` 擋資料中心 IP：本機通、GitHub Actions 不通。

**修法**：`secondary` 改成候選清單，Nasdaq 優先、StockAnalysis 備援，
第一個回得出完成 K 線的就停。另外把 `source_incidents` 明細印出來。

### 3. `verify_market_universe.py` — 賽道籃子擋住整條管線

Layer 1／DeFi／Meme 的 4 家來源（CoinGecko／CoinPaprika／CoinLore／Binance）
常只有 2 家的 24h 報酬落在 1% 內，不構成嚴格多數。Binance 用 USDT 計價、
另外兩家用 USD 聚合，口徑本來就不同，分歧是常態不是異常。

**決策（owner 2026-08-19 拍板）**：賽道廣度是脈絡指標、不是核心檢查，
一律降級不再擋管線。資料層的 fail-closed 沒有放寬——沒通過共識的籃子
仍然發 `status: unavailable` 且值為 null；而且新增一條硬檢查：
**沒通過驗證卻還發數字，照樣 hard fail**（見 `test_market_evidence_mutations.py`
的 `unverified-sector-value`）。

## 新東西：作者論點追蹤

把作者在 Substack「賺錢有道」寫的 BTC／ETH 分析，逐條拆成可對帳的
claim 與可判定的 falsifier，接進市場總編頁。

- 輸入：`data/inputs/author_theses.json`（文章寫下的數字＋觀測日，永不覆寫）
- 產出：`data/daily/author_thesis_tracker.json`
- 驗證：`scripts/verify_author_thesis_tracker.py` 自己重讀輸入與上游重算一遍
- 反脆弱：`scripts/test_author_thesis_mutations.py` 證明改寫原文數字、
  把偏離謊報成一致、把未追蹤訊號假裝已判定、偽造 summary、
  偷開 execution gate、解除 lineage 綁定——六種都會被擋下

指標對不上的部分**不補值也不换算**，直接標 `unavailable`／`untracked`
並列出原因（Coinbase 溢價、礦工 OTC 餘額、EIP-1559 銷毀量目前都沒有）。

## 還沒修的（已知、不擋管線）

- ETH DAT 持倉跨源代表性未通過 → 長期 degradation
- `verify_daily_data.py` 仍保留 `COMPOSITION_DIVERGENT_SECTORS`，
  但該步驟是 `continue-on-error`，不影響發布
- Actions 的 Node 20 淘汰警告（actions/checkout、setup-python、upload-artifact 要升版）
