# metrics-spec.md — 自算指標規格書

> 原則：**觸發框架的所有輸入必須可自算、可審計、不引用標的公司自訂口徑。** strategy.com 的官方 mNAV 降級為情緒參考。本文件定義公式、資料來源、更新頻率。

---

## 一、自算指標定義

### M1. equity mNAV（保守口徑）

```
equity_mNAV = MSTR 普通股市值 ÷ (BTC NAV + 現金及 USD Reserve − 債務面額 − 特別股清算面額總和)
```

- 分母 = 歸屬普通股股東的淨資產。**特別股按清算面額全額扣除**（不按市價——市價折價是普通股股東的或有負債，不是資產）。
- 分母 ≤ 0 時記為 `N/A(insolvent-to-common)`，本身即為❌級訊號。


### 會計口徑保護欄：GAAP NAV ≠ BTC NAV

- `BTC NAV = BTC 持倉 × 現貨價`，只是單一資產線。
- `GAAP NAV / 股東權益 = BTC 公允價值 + 現金 + 其他資產 − 可轉債 − 淨遞延稅負債 − 其他負債`；優先股若列在權益中，也不代表它屬於普通股。
- ASU 2023-08 只讓 BTC 這條資產線按公允價值入帳，**沒有**讓整張資產負債表的股東權益等於 BTC NAV。
- 因此 M1 必須扣 `net_deferred_tax_liability_musd`；BTC 高於成本區後，遞延稅負債可能系統性壓低歸屬普通股的淨值。

### M2. enterprise mNAV（官方同構口徑，自算版）

```
enterprise_mNAV = (普通股市值 + 債務面額 + 特別股清算面額) ÷ BTC NAV
```

- 與官方定義同構，但成分數字全部取自 SEC 文件而非官網儀表板。
- 用途：與官網數字對照，**兩者差異本身是資訊**（差異擴大 = 官網口徑調整的偵測器）。

### M3. 特別股稀釋旗標（pref-dilution flag）

```
flag = (本期特別股清算面額淨增量 > 0) AND (mNAV 較上期回升)
```

- 旗標為真時：該期 mNAV 回升訊號**打五折**——可能是分子工程（發 $80 市價的 STRC 記 $100 面額），不是市場重估。
- 詳見 [[mnav-definition-risk]]。

### M4. 覆蓋月數（不可操縱核心指標）

```
coverage_months = USD Reserve ÷ (年化優先股股息 + 債務利息)/12
```

- 年化義務自算：Σ(各系列清算面額 × 股息率) + 可轉債票息。目前 ≈ $1.76B/年 ≈ $147M/月。
- < 12 個月 = 觸犯公司自訂硬下限，❌。

### M5. 週賣幣比值

```
sale_ratio = 週 BTC 出售所得(USD) ÷ 週義務基準($34M)
```

- ≤1.5 ✅；>2 一次⚠️；連續兩週 >2 ❌（＝第 2 等份③「被迫賣幣」觸發）。

### M6. sats/股

```
sats_per_share = BTC 持倉(sats) ÷ assumed diluted 股數
```

- 分子分母皆變動的新階段，須同時追蹤兩者的變動方向。

### M7. STRC 折價深度

```
strc_discount = 1 − STRC 市價/100
```

- 不可操縱的優先股體系信任票。>5% 期間，M1/M2 的任何回升訊號都需 M3 檢查。

---

## 二、資料來源表

| 資料項 | 主來源 | 備援 | 更新頻率 |
| --- | --- | --- | --- |
| BTC 持倉、週賣幣/買幣、均價 | SEC EDGAR 8-K（CIK 0001050446） | saylortracker.com、bitcointreasuries.net | 每週一 |
| USD Reserve 餘額 | 週 8-K 揭露 | 官方新聞稿 | 每週一 |
| 特別股清算面額（STRF/STRC/STRK/STRD 各系列 outstanding） | 10-Q 資本結構表 + 增發 8-K 累加 | 官網（僅對照） | 每季核對、每週增量 |
| 債務面額與票息（可轉債明細） | 10-Q Notes Payable 附註 | — | 每季 |
| 普通股 assumed diluted 股數 | 10-Q 封面 + 8-K ATM 揭露累加 | — | 每週 |
| MSTR / STRF / STRC / STRK / STRD 市價 | Yahoo Finance API | stooq | 每日收盤 |
| BTC 價格 | CoinGecko API | Coinbase spot | 每日 |
| 官網 mNAV（僅對照用） | strategy.com | — | 每日，記錄與 M2 差值 |

**審計規則**：每季 10-Q 發布後，用文件數字回校週度累加值；差異 >1% 需在 monitor-log 註記原因。

---

## 三、與觸發框架的接線

| 框架條件 | 改用的自算指標 |
| --- | --- |
| 第 2 等份③ mNAV ≥ 1.0x | **M1 與 M2 同時 ≥ 1.0** 且 M3 旗標為假 |
| regime 標籤（增發/過渡/回購） | 以 M1 分界（保守口徑） |
| 「被迫賣幣」判定 | M5 連續兩週 >2 |
| 儲備健康 | M4 < 12 即❌ |
| 優先股體系信任 | M7 > 5% 期間全體 mNAV 訊號降權 |

執行工具見 `tools/mnav_calc.py`。

*非投資建議。所有觸發判定下單前人工核實 EDGAR 原文。*
