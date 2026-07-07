# delayed-pro-cyclical（延遲順週期）

**一句話**：MSTR 的 Digital Credit Capital Framework 沒有消除順週期性，只是在順週期機制前加了緩衝層——「跌了就得賣」變成「跌了可以撐 ~17 個月再賣」。

## Tranche 對映

| MSTR 結構 | 結構型基金對應物 | 行為 |
| --- | --- | --- |
| 優先股（STRF/STRC/STRK/STRD） | Senior tranche | 先領現金流、名目上風險較低 |
| MSTR 普通股 | Equity tranche | 墊底吸收全部波動與虧損 |
| BTC Monetization Program（$1.25B） | Triggered liquidation | 條件觸發時賣資產保護 senior |
| USD Reserve（$2.55B，≥12 個月覆蓋硬下限） | 緩衝墊 | 把觸發時點往後推 |

## 核心未解問題

無息資產（BTC）對應有息負債（優先股股息+債息）的錯配：
- 年化義務 ~$1.76B（由 $2.55B ÷ 17.4 個月回推）
- 對 ~$50B BTC NAV = **每年 ~3.5% 自然損耗率**
- 補血來源三選一：ATM 增發（折價期傷股東）、賣幣（傷 sats/股）、發新優先股（信用利差決定可行性）
- **BTC 橫盤越久，失血越久**——這是慢性病模型，不是死亡螺旋模型

## 隱藏前提清單（財務工程對 BTC 的真實要求）

「BTC 長期上漲」不夠。上了槓桿之後：
1. 可轉債：BTC 在**每個還款週期內**絕對上漲 + 維持高波動率（波動率是可轉債的賣點）
2. 優先股：信用利差不擴大 + 優先股不折價（STRC 跌破 $80 = 此前提破功實錄）
3. ATM 飛輪：mNAV > 1（已破功，見 [[mnav-reflexivity]]）

## 監控錨點

- STRC vs $100 面額（官方目標 $99–100）
- 週賣幣金額 vs 週股息需求（~$34M/週）比值
- $1.25B 額度使用率
- 回購執行進度（折價期公司行為方向盤）

相關：[[mnav-reflexivity]]、[[coin-per-share-accretion]]
