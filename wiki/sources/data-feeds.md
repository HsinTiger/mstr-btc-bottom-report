---
title: 數據流與權威來源
type: source
tags: [data, sources, monitor]
updated: 2026-07-28
confidence: high
summary: 每日四週期與市場總編、每小時跨資產雷達分層更新；現貨、ETF、DAT、宏觀與鏈上資料各自保留 as_of 並通過可重算契約。
---

# 數據流與權威來源

[[five-dimension-model]] 與跨資產市場雷達皆採「先溯源、再判讀」。決策管線每日更新，市場雷達每小時更新；來源缺失或分歧時維持未知，不沿用舊綠燈。

| 維度 | 主來源 | 備援 |
|---|---|---|
| 🪙 籌碼面 | CryptoQuant、Glassnode | Coinglass、Farside、SoSoValue、IntoTheBlock |
| 📈 技術面 | TradingView、Bitcoin Magazine Pro | CheckOnChain、LookIntoBitcoin、Barchart |
| 📉 週期遞減 | Bitcoin Magazine Pro、Bitbo | Cowen/IntoTheCryptoverse、LookIntoBitcoin、TradeThatSwing |
| 😱 市場情緒 | Alternative.me、Santiment | Coinglass、Google Trends、The Tie |
| 🏛️ 基本面發展 | Coinbase 報告、RWA.xyz | DeFiLlama、SoSoValue、Electric Capital、stockanalysis.com |

## 跨資產市場雷達

| 資料 | 已接來源 | 治理限制 |
|---|---|---|
| BTC／ETH／SOL／BNB／XRP／DOGE 現貨 | CoinGecko、Coinbase、OKX；Binance 可用時加入 | 至少雙來源；USDT 先換算 USD；價差超過 2% 即 fail |
| HYPE 現貨／永續 | CoinGecko、Coinbase、OKX／Hyperliquid | 現貨與永續分開；永續標記價不算現貨交叉來源 |
| BTC／ETH 永續 | Bybit、OKX、Hyperliquid；Binance 可用時加入 | 各場域依自身週期年化後取中位；合計只稱可觀測場域 |
| BTC／ETH 約三個月到期期貨 | Deribit 掛牌月到期合約；受限時 OKX 幣本位到期合約 | 選離 90 日最近合約並依實際到期日年化；OKX 用買賣中價相對指數價，不與永續資金費率混為一談 |
| CME 期貨代理 | Yahoo Finance `BTC=F`／`ETH=F` | 可能延遲與換月，只作背景，不作成交價 |
| BTC／ETH 期權 | Deribit 幣本位 options＋DVOL；受限時 OKX 幣本位 options | OKX 備援用近 30 日 ATM Call/Put 標記 IV 平均，不冒充 DVOL；兩供應商序列分開，Put/Call 與 max pain 不是方向預測 |
| BTC／ETH DAT | CoinGecko、BitcoinTreasuries.net、Bitbo Bitcoin Treasuries（BTC 驗證用）、SEC 官方前列公司揭露 | aggregate canonical 可在 CoinGecko／BitcoinTreasuries.net 間切換；至少兩家可比公司、代表性覆蓋 60%、加權差異 1%、單公司共識差異 5%。超標舊值保留為 outlier 但不污染共識；官方公司缺失或無法映射即不通過 |
| BTC／ETH 美國現貨 ETF | The Block、Blockworks／Trackinsights、Bitbo／WalletPilot（BTC）、CoinMarketCap ETF、iShares IBIT／ETHA 官方持倉 | canonical 由具完整基金 roster 的最新來源動態選擇；至少三源、官方大基金與同日基金／總量備援差異不高於 5% 或 500 萬美元，各覆蓋 gross flow 30%。基金列與總量只容許 50 萬美元或 0.1% 四捨五入差；只發布最近完成官方 T+1 驗證的市場日 |
| RWA／Layer 1／DeFi／Meme | CoinGecko Markets、CoinPaprika Tickers、Binance Data API | `fixed-basket-v1` 每類固定五個成分；每來源先算成分報酬中位數，再算跨來源中位數。至少雙來源、差異不高於 1 個百分點；市值與成交量只採兩家全市場聚合站，Binance 僅驗證報酬 |
| MSTR 資本結構 | SEC 10-Q／10-K、後續 8-K、Strategy 官方 purchases ledger | 季報重算債務面額、票息、五檔優先股（含 STRE）；逐週 8-K ATM 期間必須連續，並用官方聚合債務／優先股總額交叉。普通股採 Strategy point-in-time basic shares，SEC 封面為備援 |

## BTC 長期貨幣化論證層

| 完整指標 | 已接來源 | 治理限制 |
|---|---|---|
| BTC 市值對黃金代理總值比例 | CoinGecko BTC 市值、Yahoo `GC=F`、World Gold Council 地上存量 | 黃金期貨代理受換月影響；情境價不是目標價或等額資金需求 |
| 美元穩定幣供給與 30 日變化 | DefiLlama timestamped Stablecoin Charts | 只計 `peggedUSD`；逐資產快照另作同源對帳，不是存款、成交量或美元整體供給 |
| RWA 協議 TVL | DefiLlama Protocols `category=RWA`＋前五大協議歷史 | 供應商分類，可能重複計算；前五大歷史時間戳只驗證新鮮度，不與穩定幣供給相加 |
| 鏈上 BTCFi 抵押／生息代理 | DefiLlama Protocols 的 `Anchor BTC`、`Restaked BTC`、`Decentralized BTC` | 只代表可觀測鏈上協議 TVL；不含中心化借貸、銀行抵押、衍生品保證金與再質押重複計算，不冒充全球總量 |
| 公開公司 BTC 持幣滲透與集中度 | CoinGecko、BitcoinTreasuries.net、Strategy SEC 揭露、Coin Metrics BTC 供給 | 先過 DAT 公司交集驗證；仍不含 ETF、政府、私人公司、託管與抵押品重複使用 |
| 算力 30 日變化與相對 90 日高點 | Blockchain.com hash-rate | 安全與礦工承諾代理，不是價格訊號 |
| 美國債務／GDP 與 10 年實質利率 | FRED `GFDEGDQ188S`、`DFII10` | 結構信用壓力與週期機會成本分開判讀 |

這一層對應 [[btc-neutral-anchor]]，只作長期結構背景，不進入 [[five-dimension-model]] 的底部標準分。

## 每日市場總編研究層

| 研究桌 | 主來源與備援 | 驗證限制 |
|---|---|---|
| BTC／ETH 核心市場與技術籌碼 | 四來源現貨、OKX／Hyperliquid 永續、雙來源完成日 K | 價格、跨資產廣度與槓桿分開投票；日線不得冒充月線結論 |
| ETF／DAT | ETF 三來源＋iShares；DAT 多來源＋SEC 公司 overlay | ETF 是流量、DAT 是存量；流入而價格下跌仍需標示吸收失敗 |
| 加密法案與政策 | Congress.gov、Federal Register、SEC、Federal Reserve、White House | 提案、新聞稿、正式規則與生效法律分層；事件數不等於政策利多 |
| BTC 鏈上 | Blockchain.com、Blockchair、mempool.space、Coin Metrics | 交易數與算力做跨來源差異；MVRV 只作估值背景 |
| ETH 鏈上 | PublicNode、1RPC、Flashbots；LlamaRPC 為額外備援 | 至少雙 RPC 高度差不超過 3，抽樣區塊 hash 與交易數須一致；抽樣不冒充全日總量 |
| 美元流動性與聯準會 | Fed H.4.1／FRED `WALCL`、`M2SL`、`WRESBAL`、NY Fed RRP、Treasury FiscalData TGA | Fed 淨流動性看 30 日政策脈衝、銀行準備金看金融體系脈衝、美國廣義貨幣供給看月頻慢速背景；廣義貨幣資料可容許 120 日但必須顯示原始 `as_of`，三者只投票判斷共振，不混成黑箱指數 |
| 原油、信用與債券 | FRED WTI／Yahoo `CL=F`、ICE BofA OAS、Treasury XML／FRED | 現貨油與期貨只比方向；HYG／IEF 只作 OAS 失效時的備援代理 |
| 美股與波動率 | FRED SP500／NASDAQCOM／VIXCLS、Yahoo 同標的核對 | 同標的才比較 level；VIX 只描述避險定價，不是方向預測器 |

`market_editorial.json` 的每個數字由 verifier 從上述目前 artifact 重算；`market_editorial_verification.json` 同時綁定完整 editorial hash 與 history head。來源只支持原始數據，不背書本站的非共識假說。

## 內部產物（raw/ 層）
- `../data/daily/timescale_price_history.json` — 多來源完整日 K 與日／週／月／季時序基礎
- `../data/daily/timescale_intelligence_history.json` — append-only 洞察與同日 revision
- `../monitor-log.md` — 每日完整報告追加
- `../raw/kol-subtitles/` — KOL 影片字幕（[[kol-roster]]）
- `../data-snapshot-2026-06-08.md` — 早期價格/持倉快照
- `../data/daily/market_universe.json` — 每小時跨資產現貨、衍生品、ETF、DAT 與賽道快照
- `../data/daily/market_universe_history.json` — 每日最後一筆跨資產歷史序列

## 換來源規則
來源失準或停更時，先依欄位契約切換 canonical，再以同一套來源數、日期、差異、完整度與合理範圍重驗；不得只因換到備援就自動升格為通過。每日 raw、snapshot、market universe 與 verifier 以共同 `batch_id`／生成時間綁定，任何跨批次混用皆失敗。
