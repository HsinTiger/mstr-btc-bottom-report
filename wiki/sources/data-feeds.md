---
title: 數據流與權威來源
type: source
tags: [data, sources, monitor]
updated: 2026-06-14
confidence: high
---

# 數據流與權威來源

[[five-dimension-model]] 各維的權威數據平台（先溯源、再判讀）。每日監控排程（08:00 台北）自動抓取，append 至 `../data/history.json`、`../monitor-log.md`。

| 維度 | 主來源 | 備援 |
|---|---|---|
| 🪙 籌碼面 | CryptoQuant、Glassnode | Coinglass、Farside、SoSoValue、IntoTheBlock |
| 📈 技術面 | TradingView、Bitcoin Magazine Pro | CheckOnChain、LookIntoBitcoin、Barchart |
| 📉 週期遞減 | Bitcoin Magazine Pro、Bitbo | Cowen/IntoTheCryptoverse、LookIntoBitcoin、TradeThatSwing |
| 😱 市場情緒 | Alternative.me、Santiment | Coinglass、Google Trends、The Tie |
| 🏛️ 基本面發展 | Coinbase 報告、RWA.xyz | DeFiLlama、SoSoValue、Electric Capital、stockanalysis.com |

## 內部產物（raw/ 層）
- `../data/history.json` — 五維綜合溫度時間序列（前端「歷史趨勢」繪圖）
- `../monitor-log.md` — 每日完整報告追加
- `../raw/kol-subtitles/` — KOL 影片字幕（[[kol-roster]]）
- `../data-snapshot-2026-06-08.md` — 早期價格/持倉快照

## 換來源規則
發現主來源失準/停更 → 改用備援，並同步更新每日監控 prompt 的對應平台名（見 [[CLAUDE]] schema）。
