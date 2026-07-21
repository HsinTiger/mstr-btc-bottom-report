---
title: 數據流與權威來源
type: source
tags: [data, sources, monitor]
updated: 2026-07-21
confidence: high
summary: 每日決策資料與四小時跨資產雷達分層更新；現貨要求交叉來源，衍生品保留交易所口徑，ETF 與 DAT 聚合資料不得直接當硬觸發。
---

# 數據流與權威來源

[[five-dimension-model]] 與跨資產市場雷達皆採「先溯源、再判讀」。決策管線每日更新，市場雷達每四小時更新；來源缺失或分歧時維持未知，不沿用舊綠燈。

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
| BTC ETF | WalletPilot | 第三方單源，權重降級且不計硬確認票 |
| ETH ETF | 尚無穩定免金鑰交叉來源 | 顯示未知，不硬爬動態 HTML 補值 |
| BTC／ETH DAT | CoinGecko public treasury 單一聚合來源 | 聚合總量與前八家公司只作雷達；差額可能含供應商修訂，尚未逐筆官方驗證 |
| RWA／Layer 1／DeFi／Meme | CoinGecko Categories | 分類由供應商定義，只看相對輪動 |

## BTC 長期貨幣化論證層

| 完整指標 | 已接來源 | 治理限制 |
|---|---|---|
| BTC 市值對黃金代理總值比例 | CoinGecko BTC 市值、Yahoo `GC=F`、World Gold Council 地上存量 | 黃金期貨代理受換月影響；情境價不是目標價或等額資金需求 |
| 美元穩定幣供給與 30 日變化 | DefiLlama Stablecoins | 只計 `peggedUSD`；不是存款、成交量或美元整體供給 |
| RWA 協議 TVL | DefiLlama Protocols `category=RWA` | 供應商分類，可能重複計算；不與穩定幣供給相加 |
| 公開公司 BTC 持幣滲透與集中度 | CoinGecko Public Companies Treasury、Coin Metrics BTC 供給 | 不含 ETF、政府、私人公司、託管與抵押品重複使用 |
| 算力 30 日變化與相對 90 日高點 | Blockchain.com hash-rate | 安全與礦工承諾代理，不是價格訊號 |
| 美國債務／GDP 與 10 年實質利率 | FRED `GFDEGDQ188S`、`DFII10` | 結構信用壓力與週期機會成本分開判讀 |

這一層對應 [[btc-neutral-anchor]]，只作長期結構背景，不進入 [[five-dimension-model]] 的底部標準分。

## 內部產物（raw/ 層）
- `../data/history.json` — 五維綜合溫度時間序列（前端「歷史趨勢」繪圖）
- `../monitor-log.md` — 每日完整報告追加
- `../raw/kol-subtitles/` — KOL 影片字幕（[[kol-roster]]）
- `../data-snapshot-2026-06-08.md` — 早期價格/持倉快照
- `../data/daily/market_universe.json` — 四小時跨資產現貨、衍生品、ETF、DAT 與賽道快照
- `../data/daily/market_universe_history.json` — 每日最後一筆跨資產歷史序列

## 換來源規則
發現主來源失準/停更 → 改用備援，並同步更新每日監控 prompt 的對應平台名（見 [[CLAUDE]] schema）。
