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
| BTC／ETH／SOL／BNB／XRP／DOGE 現貨 | CoinGecko、Binance、Coinbase | 至少雙來源；價差超過 2% 即 fail |
| HYPE 現貨／永續 | CoinGecko、Coinbase／Hyperliquid | 現貨與永續分開；永續標記價不算現貨交叉來源 |
| BTC／ETH 永續 | Binance USD-M、Bybit Linear | 資金費率與 OI 保留場域口徑；合計只稱可觀測場域 |
| BTC／ETH 季度期貨 | Binance COIN-M | 基差依到期日年化；不與永續資金費率混為一談 |
| CME 期貨代理 | Yahoo Finance `BTC=F`／`ETH=F` | 可能延遲與換月，只作背景，不作成交價 |
| BTC／ETH 期權 | Deribit 幣本位 options＋DVOL | 不含 USDC 期權；Put/Call 與 max pain 是部位集中度，不是方向預測 |
| BTC ETF | WalletPilot | 第三方單源，權重降級且不計硬確認票 |
| ETH ETF | 尚無穩定免金鑰交叉來源 | 顯示未知，不硬爬動態 HTML 補值 |
| BTC／ETH DAT | CoinGecko public treasury 單一聚合來源 | 聚合總量與前八家公司只作雷達；差額可能含供應商修訂，尚未逐筆官方驗證 |
| RWA／Layer 1／DeFi／Meme | CoinGecko Categories | 分類由供應商定義，只看相對輪動 |

## 內部產物（raw/ 層）
- `../data/history.json` — 五維綜合溫度時間序列（前端「歷史趨勢」繪圖）
- `../monitor-log.md` — 每日完整報告追加
- `../raw/kol-subtitles/` — KOL 影片字幕（[[kol-roster]]）
- `../data-snapshot-2026-06-08.md` — 早期價格/持倉快照
- `../data/daily/market_universe.json` — 四小時跨資產現貨、衍生品、ETF、DAT 與賽道快照
- `../data/daily/market_universe_history.json` — 每日最後一筆跨資產歷史序列

## 換來源規則
發現主來源失準/停更 → 改用備援，並同步更新每日監控 prompt 的對應平台名（見 [[CLAUDE]] schema）。
