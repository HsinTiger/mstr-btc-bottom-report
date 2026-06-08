# 資料快照 — 2026/6/8（MSTR vs BMNR 查證）

抓取時間：2026-06-08 ~02:00 UTC。腳本：Yahoo Finance chart API（價格/52週區間）+ WebSearch/WebFetch（持倉/股數/mNAV）。可重現。

## 即時價格（Yahoo Finance v8 chart API）

| 標的 | 現價 | 52週高 | 52週低 | 距52週高 |
|---|---|---|---|---|
| MSTR | $120.44 | $457.22 | $104.17 | -73.7% |
| BMNR | $15.90 | $161.00 | $3.92 | -90.1% |
| BTC-USD | $63,160 | $126,198 | $60,074 | -49.9% |
| ETH-USD | $1,685 | $4,954 | $1,674 | -66.0% |

ETH/BTC ≈ 0.0267（多年低位，ETH 相對 BTC 很便宜）。

## 公司財庫（WebSearch + 官方PR + 追蹤站）

**MSTR（Strategy）**
- BTC 持倉：843,706 BTC（2026/6/1）≈ 全網 4.0%
- 最近買入：2026/5/11 買 535 BTC
- 市值：$42.35B；股數：351.6M（stockanalysis.com）
- BTC NAV @ $63,160 = 843,706 × 63,160 ≈ **$53.3B**
- **mNAV（普通股市值 ÷ BTC NAV）= 42.35 / 53.3 ≈ 0.80x（折價）**
- 特別股：STRC 104.89M 股 + STRF 12.84M + STRK 14.02M + STRD 14.02M；合計 notional ~$15B、市值 ~$13.25B（bitcoinquant）
- 剛性年支出：可轉債加權票息僅 0.42%（年息 ~$35M，可忽略）；**特別股股息才是大頭**——STRC 11.5% 變動利率，單 STRC ≈ $1B/年，全部特別股合計 ~$0.9–1.5B/年（來源分歧，趨勢隨 STRC 增發上升）
- 可轉債：總額 ~$7–8B，加權票息 0.42%；最近一筆較大到期是 **$1.01B、0.625%、2028/9/15 到期**（非「2027 $10B 牆」）

**BMNR（BitMine，Tom Lee）**
- ETH 持倉：5,416,901 ETH（5/31）≈ 全網 4.49%；5,390,404 ETH（~6/4）
- 質押：4,718,677 ETH（~87% 已質押）
- 市值：$9.06B；股數：569.58M（stockanalysis.com）
- ETH NAV @ $1,685 ≈ 5.39M × 1,685 ≈ **$9.08B**（+現金）
- **mNAV ≈ 9.06 / 9.08 ≈ ~1.0x（近平價，The Block 報 0.99）**
- 特別股：BMNP ~$280–350M、9.5%（年息 ~$30M）
- 質押年收益（@~3%）：4.72M × $1,685 × 3% ≈ **$238M/年** → 覆蓋 BMNP 股息 ~8x（自償能力強）
- 幾乎零傳統債務

## 「相較歷史是否便宜」判定
- **BTC**：-50% from ATH、貼 200 週均線；但 MVRV-Z ~0.41 = 公允價，非深底。便宜但非血流成河。
- **ETH**：-66% from ATH，跌更兇；ETH/BTC 0.027 在多年低位 → 相對 BTC 歷史級便宜（但也反映 ETH 較弱）。
- **MSTR**：股價 -74%；mNAV 0.80x，對比歷史常態 1.5–3x 溢價 → 以 mNAV 看是歷史級便宜（≈8 折買 BTC），但折價反映 $15B 特別股壓頂 + 反身性風險。
- **BMNR**：股價 -90%；mNAV ~1.0x，溢價已從高峰（曾 >2x）洩到平價 → 比高峰便宜很多，但「沒有折價安全墊」。

## 來源
- Yahoo Finance chart API（MSTR/BMNR/BTC-USD/ETH-USD）
- stockanalysis.com（MSTR/BMNR 市值、股數）
- bitcoinquant.co/company/MSTR（特別股結構）
- BMNR 官方 PR（PRNewswire，ETH 5.42M / 4.49% / 質押 4.72M）
- CoinDesk 2026/1/22（特別股市值超過可轉債）、Nasdaq/Strategy PR（$1.01B 2028 可轉債）、PANews（債務結構）
</content>
</invoke>
