---
title: 五維共振模型
type: concept
tags: [framework, signal, temperature]
updated: 2026-06-14
confidence: high
---

# 五維共振模型

評估 BTC/ETH 底部與頂部的自有框架。每維各投一票（**便宜/投降為負＝該買；貴/狂熱為正＝該賣**），合成 −10（投降底）→ +10（狂熱頂）的**綜合溫度**。五維同向才極端；任一維背離＝「還沒到」。`extends` 早期的招財貓兩層（鏈上+mNAV），補上邊際買盤與槓桿定位。

| 維度 | 看什麼 | 權威數據源（見 [[data-feeds]]） |
|---|---|---|
| 🪙 籌碼面 | 交易所存量、鯨魚、LTH、ETF 流、funding | CryptoQuant、Glassnode、Coinglass |
| 📈 技術面 | MVRV-Z、Mayer、200日線、Pi Cycle、NUPL | TradingView、Bitcoin Magazine Pro |
| 📉 週期遞減 | 距 ATH 回撤、漲跌幅遞減、底部區 | BMPro、Bitbo、Cowen（見 [[cycle-diminishing-returns]]）|
| 😱 市場情緒 | Fear&Greed、社群、多空比 | Alternative.me、Santiment |
| 🏛️ 基本面發展 | RWA/穩定幣、ETF AUM、DAT 財庫 | Coinbase、RWA.xyz、DeFiLlama |

## 計分→行動
- −10~−6：三/五維共振投降底 → 投 [[two-tranche-plan]] 第 1 等份
- −5~−2：便宜但未投降 → 小額 DCA（**現況 ~−3**）
- +2~+5：趨勢確認未過熱 → 投第 2 等份
- +6~+10：共振狂熱頂 → 分批止盈、先減 [[bmnr]]

歷史走勢記錄於 `../data/history.json`，前端「歷史趨勢」分頁繪圖。`supports` [[two-tranche-plan]] 的觸發條件。
