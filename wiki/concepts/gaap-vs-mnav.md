---
title: GAAP、Non-GAAP 與市場價格對淨資產價值
type: concept
tags: [gaap, non-gaap, mnav, accounting]
updated: 2026-07-21
confidence: med
summary: BTC 總值、GAAP 股東權益與公司自訂 mNAV 是三本不同的帳；普通股估值還要扣債務、優先股與稅務楔子。
---

# GAAP、Non-GAAP 與市場價格對淨資產價值

**一句話**：GAAP 是法定財報語言，Non-GAAP 是公司自訂溝通語言；mNAV、BTC Yield 與 BTC Dollar Gain 可以有資訊價值，但不能當成查核過的普通股安全邊際。

## 三條公式不要混用

### BTC 總值

```text
BTC 總值 = BTC 持倉 × BTC 現貨價
```

這只是 Strategy 資產負債表裡的 BTC 資產線，不含現金、不扣債、不扣稅，也不處理優先股。

### GAAP 股東權益

```text
GAAP 股東權益 = BTC 公允價值 + 現金 + 其他資產 − 可轉債 − 淨遞延稅負債 − 其他負債
```

ASU 2023-08 讓 crypto assets 以公允價值入帳，但只改變 BTC 資產線，沒有把整張資產負債表簡化成 BTC 總值。優先股若列在權益內，也不代表該價值屬於普通股。

### 公司自訂 KPI

mNAV、BTC Yield 與 BTC Dollar Gain 都是 Non-GAAP 指標。它們有助理解財庫反身性與每股含幣量敘事，但定義權在公司手上，且不是會計師查核的 GAAP 數字。

## 對持有分析的含義

1. **普通股不是優先順位資產**：估值必須扣除債務、優先股清算面額及已揭露稅務楔子，或清楚標示未扣項目。
2. **官方 mNAV 是行為變數**：可用來觀察發行與回購誘因；現金流、股息覆蓋、債務條款與稅務才是硬約束。
3. **MSTR 小倉合約更保守**：2.5 倍波段只吃右側確認，不在定義混濁、STRC 折價或賣幣資料未知時賭反身性。
4. **大倉現貨看 1–4 年**：MSTR 與 BMNR 採 4:1，但 BTC／ETH beta、每股含幣量、稀釋與資本結構品質分開判斷。

## 對 BMNR 的限制

BMNR 目前只能回答 gross treasury 與每股 ETH，不得只用「ETH 版 MSTR」或 gross assets 折價宣稱普通股淨 NAV 安全邊際。完整負債、優先順位與潛在稀釋未自動解析前，一律維持研究級。

## 操作規則

- 看到公司自創 KPI，先問：10-Q 找得到嗎？定義誰控制？能否被發行結構機械式推動？
- 更新「普通股市值／普通股淨值」時，必須檢查最新 10-Q／10-K 的股數、債務、優先股與淨遞延稅負債或資產。
- 若必要資料缺失，前端只能標示「資料不足」並封鎖交易放行，不能顯示成精確安全邊際。

相關：[[mnav-definition-risk]]、[[mnav-reflexivity]]、[[two-tranche-plan]]
