---
title: 含幣量增厚
type: concept
tags: [btc-yield, accretion, dilution]
updated: 2026-07-22
last_verified: 2026-07-22
confidence: high
summary: 每股含幣量必須用可追溯股數重算；公司自訂 BTC Yield 與本站基本股每股含幣量不可混為同一口徑。
---

# 含幣量增厚（BTC/ETH per share）

使用者最看重的指標：持有 DAT 是否讓**每股對應的幣**越來越多。

## MSTR — 基本股口徑

2026-07-19 官方持幣 **843,775 BTC**；Strategy point-in-time basic shares 加後續連續 ATM 8-K 台帳為 **379.165099M 股**。本站重算：

```
基本股每股聰數 = 843,775 × 100,000,000 ÷ 379,165,099 = 222,535 sats/股
```

公司自訂 **BTC Yield** 使用自己的假設稀釋股數與期間定義，適合描述公司 KPI，但不可與本站 point-in-time basic-share 指標直接串成同一時間序列。是否真正增厚，要同時核對持幣、股數、可轉債／獎酬稀釋與 [[strc-preferred]] 現金義務。

## BMNR — 買回調整估計口徑

2026-07-19 官方持有 **5,777,468 ETH**；SEC 股數 603.226394M 扣除股數基準日後揭露的 5.5M 買回，估計 **597.726394M 股**：

```
每千股 ETH = 5,777,468 × 1,000 ÷ 597,726,394 = 9.666 ETH
```

這是買回調整估計，不是最新 transfer-agent 精確股數。質押能增加 ETH，但若發股速度更快，每股含幣量仍會下降；因此每日同時追蹤 ETH 持倉、股數、買回與質押比例。

→ 支持 [[mstr]] : [[bmnr]] = 0.80 : 0.20（4:1）偏 MSTR。每日 sats/股、ETH/股由 [[data-feeds]] 追蹤。
