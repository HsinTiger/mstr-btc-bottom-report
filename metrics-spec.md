# metrics-spec.md — 自算指標規格書

> 原則：**觸發框架的所有輸入必須可自算、可審計、不引用標的公司自訂口徑。** strategy.com 的官方 mNAV 降級為情緒參考。本文件定義公式、資料來源、更新頻率。

---

## 一、自算指標定義

### 普通股市值／普通股淨值（保守口徑）

```
common_equity_price_to_nav = MSTR 普通股市值 ÷ (BTC NAV + 現金及 USD Reserve − 債務面額 − 特別股清算面額總和 − 已揭露遞延稅負債)
```

- 分母 = 歸屬普通股股東的淨資產。**特別股按清算面額全額扣除**（不按市價——市價折價是普通股股東的或有負債，不是資產）。
- 分母 ≤ 0 時記為 `N/A(insolvent-to-common)`，本身即為❌級訊號。
- 這是 `Price/NAV`：**倍率愈低，估值相對愈便宜**。`>1.0x` 是溢價，不得稱為安全邊際改善；`≤1.0x` 也只代表折價背景，不會單獨觸發買進。
- 分子採最新 10-Q/10-K 封面 inline XBRL 的各類普通股實際流通股數總和 × 市價；不得用 EPS 的季度加權稀釋股數替代，避免再全額扣債時重複計入可轉債轉換稀釋。


### 會計口徑保護欄：GAAP NAV ≠ BTC NAV

- `BTC NAV = BTC 持倉 × 現貨價`，只是單一資產線。
- `GAAP NAV / 股東權益 = BTC 公允價值 + 現金 + 其他資產 − 可轉債 − 淨遞延稅負債 − 其他負債`；優先股若列在權益中，也不代表它屬於普通股。
- ASU 2023-08 只讓 BTC 這條資產線按公允價值入帳，**沒有**讓整張資產負債表的股東權益等於 BTC NAV。
- 因此普通股淨值必須扣 `deferred_tax_liability_musd`。SEC 自動來源優先採資產負債表 `DeferredIncomeTaxLiabilitiesNet`，舊 tag 僅作備援；這是已列報負債，不自行把未確認稅務假設塞入估值。

### 企業價值／BTC 總值（官方同構口徑，自算版）

```
enterprise_value_to_btc_nav = (普通股市值 + 債務面額 + 特別股清算面額 − 現金及 USD Reserve) ÷ BTC NAV
```

- 與官方定義同構，但成分數字全部取自 SEC 文件而非官網儀表板。
- 用途：觀察企業層 BTC 溢價與資本飛輪，不代表普通股便宜。與官網數字的差異本身是資訊。

### 特別股稀釋污染旗標（pref-dilution flag）

```
flag = (本期特別股清算面額淨增量 > 0) AND (普通股 Price/NAV 較上期上升)
```

- 旗標為真時：該期 Price/NAV 上升可能來自分母被新增優先股壓低，不可誤讀為市場重估或安全邊際改善。
- 詳見 [[mnav-definition-risk]]。

### 明示固定義務覆蓋月數

```
coverage_months = USD Reserve ÷ (年化優先股股息 + 債務利息)/12
```

- 年化義務自算：Σ(各系列清算面額 × 股息率) + 可轉債票息。數值隨資本結構每日重算，不在文件硬編金額。
- < 12 個月 = 觸犯公司自訂硬下限，❌。
- 這不是完整 liquidity runway：尚未納入營運現金消耗、稅款、到期本金、贖回與新發行；USD Reserve 超過 30 日未更新即降級，超過 120 日視為失效。

### 每週賣幣壓力倍數

```
sale_ratio = 官方 purchases ledger 最近 7 日 BTC 出售所得(USD) ÷ [(年化優先股股息 + 債務利息) / 52]
```

- 最近一筆交易不得冒充最近 7 日交易；超出 7 日事件窗後歸零。
- ≤1.5 ✅；>2 一次⚠️；連續兩週 >2 ❌（＝第 2 等份③「被迫賣幣」觸發）。

### 每股比特幣含量

```
sats_per_share = BTC 持倉(sats) ÷ assumed diluted 股數
```

- 分子分母皆變動的新階段，須同時追蹤兩者的變動方向。

### STRC 優先股折價信任票

```
strc_discount = 1 − STRC 市價/100
```

- 優先股市場的資本結構信任票。>5% 期間封鎖 MSTR 合約，普通股與企業層倍率的樂觀解讀一律降權。

### BTC 五維標準分

以 `−10`（偏冷／投降）到 `+10`（偏熱／追高）標準化，五維為：MVRV 估值、相對 200DMA 趨勢、Fear & Greed、ETF 邊際流量、1 年週期回撤。各維度以線性函數映射到 `−2…+2` 並截尾：MVRV `1.0→−2、2.2→+2`；200DMA 偏離 `−15%→−2、+15%→+2`；F&G `25→−2、75→+2`；ETF 7 日流量 `−$500M→−2、+$500M→+2`；1 年回撤 `−45%→−2、−10%→+2`。加權和再除以 `2 × 可用權重` 並乘 10。

- BTC 市場分數不得混入 MSTR 或 BMNR 資本結構風險。
- 權重：估值 `1.25`、趨勢 `1.0`、情緒 `0.75`、ETF `0.5`、回撤 `1.0`。ETF 必須通過完整基金 roster、iShares 官方主要基金與同日獨立備援的三來源契約；即使通過仍只作低權重背景，不得單獨成為右側確認或交易硬觸發。MSTR/BMNR 另列 implementation overlay，不得進入 BTC 投降票數。
- 資料覆蓋低於 80% 時，狀態只能是「資料不足觀察區」。
- 模型狀態是 `heuristic_unbacktested`：這是制度化 regime context，不是已證明有 alpha 的預測模型；完成 walk-forward 回測、交易成本與樣本外基準前，不可稱為已驗證訊號。

### BTC 非主權價值錨論證指標

這組指標回答 BTC 是否持續貨幣化與金融化，**不得進入 BTC 五維底部標準分**。

```
BTC 對黃金代理總值比例
= BTC 市值 ÷（COMEX 黃金前月代理價 × WGC 地上黃金公噸 × 32,150.746568627 金衡盎司/公噸）

黃金占比情境 BTC 價
= 黃金代理總值 × 情境占比(25%/50%/100%) ÷ BTC 流通供給

BTC 對美元穩定幣規模比
= BTC 市值 ÷ DefiLlama 美元掛鉤穩定幣供給

美元穩定幣 30 日變化
= 同時具備今日與 30 日前數值的同群穩定幣今日供給 ÷ 同群 30 日前供給 − 1

BTC 在 BTC＋穩定幣中的規模占比
= BTC 市值 ÷（BTC 市值 + 美元掛鉤穩定幣供給）

RWA 協議可觀測總鎖倉價值
= DefiLlama 分類為 RWA 的各協議 TVL 加總

鏈上 BTCFi 抵押／生息代理
= DefiLlama 分類為 Anchor BTC、Restaked BTC、Decentralized BTC 的協議 TVL 加總

公開公司持幣滲透率
= CoinGecko 公開公司 BTC 總持倉 ÷ BTC 流通供給

公開公司持幣集中度
= 樣本最大公司 BTC 持倉 ÷ CoinGecko 公開公司 BTC 總持倉

算力相對 90 日高點
= 最新算力 ÷ 最近 90 日最高算力

算力 30 日變化
= 最新算力 ÷ 30 日前最近一期算力 − 1

美國聯邦債務占 GDP
= FRED `GFDEGDQ188S` 最新季度百分比

美國 10 年實質利率
= FRED `DFII10` 最新可用百分比
```

- 黃金代理值是情境模型，不是可投資總市值；情境價不是目標價，也不能把市值增量誤稱為所需淨流入。
- RWA 只列 DefiLlama `RWA` 類別協議 TVL，不與穩定幣供給相加，避免分類與重複計算風險。
- 債務／GDP 只作慢速主權信用壓力代理，不能證明 BTC 需求；10 年實質利率是 BTC 無現金流特性的週期機會成本，兩者可能方向相反。
- 全球 BTC 抵押信用存量目前沒有完整可去重公開資料；首頁改顯示三類 BTCFi 協議 TVL 作「可觀測鏈上代理」，並明示不含中心化借貸、銀行抵押、衍生品保證金與再質押重複計算。ETF、DAT 與衍生品 OI 不得冒充抵押採用。
- 敘事狀態門檻是未回測的描述性 heuristic：BTC／黃金 `<10%` 為早期、`10–50%` 為規模化、`>=50%` 為接近成熟；算力需同時為 90 日高點 `>=85%` 且 30 日跌幅不超過 `10%` 才稱穩固，低於 90 日高點 `70%` 才稱明顯回落；美國債務／GDP `>=100%` 與 10 年實質利率 `>=2%` 分別標記結構壓力與週期逆風。任一必要值缺漏即為未知。
- 本層使用獨立 `structural_context_quality`；缺漏、逾時或公式錯誤只能封鎖研究卡，不得降低執行層 `agent_verification_report.status` 或改變任何交易閘門。

### BMNR 市值／gross treasury

```
gross_treasury = ETH 持倉 × ETH 現價 + BTC 持倉 × BTC 現價 + 現金與市場證券 + 明示其他持股
market_cap_to_gross_treasury = 回購調整後估計市值 ÷ gross_treasury
```

- 持倉與回購來自最新 SEC 8-K Exhibit 99.1；股數來自 SEC companyfacts，若回購發生在股數日期後才做調整。
- 這是 **gross-asset view**，未扣完整負債、優先股與或有項目，不得稱為 BMNR 普通股淨 NAV 或安全邊際。

---

## 二、資料來源表

| 資料項 | 主來源 | 備援 | 更新頻率 |
| --- | --- | --- | --- |
| BTC 持倉、7 日賣幣/買幣、均價 | Strategy purchases 官方 ledger + SEC 8-K | — | 每日抓取、7 日滾動 |
| USD Reserve 餘額 | 週 8-K 揭露 | 官方新聞稿 | 每週一 |
| 特別股清算面額（STRF/STRC/STRE/STRK/STRD 各系列 outstanding） | 10-Q 資本結構表 + 連續週期 8-K ATM 累加 | 8-K 官方聚合優先股總額交叉 | 每季基準、每週增量 |
| 債務面額與票息（可轉債明細） | 10-Q Notes Payable 附註 | — | 每季 |
| 普通股實際流通股數 | 最新 10-Q/10-K 封面 inline XBRL，各普通股類別加總 | Nasdaq market cap 反推僅交叉檢查 | 每日抓取；超過 45 日降級 |
| MSTR / STRF / STRC / STRK / STRD 市價 | Yahoo Finance API | stooq | 每日收盤 |
| BTC 價格 | CoinGecko API | Coinbase spot | 每日 |
| ETH 價格 | CoinGecko API | Coinbase spot | 每日 |
| BTC MVRV | Coin Metrics community API | — | 每日；超過 3 日降級 |
| 黃金代理總值 | World Gold Council 地上存量 × Yahoo `GC=F` | — | 每小時檢查；上游日線／年度存量 |
| 美元穩定幣供給／RWA 協議 TVL | DefiLlama Stablecoins／Protocols | — | 每小時檢查；上游日更、36 小時新鮮度契約 |
| BTC 算力持續性 | Blockchain.com 180 日 hash-rate | — | 每小時檢查；超過 72 小時降級 |
| 美國債務／GDP、10 年實質利率 | FRED `GFDEGDQ188S`／`DFII10` | — | 季度／交易日 |
| BTC／ETH 到期期貨基差 | Deribit 月到期合約 | OKX 幣本位到期合約 | 每小時；依供應商分開標示 |
| BTC／ETH 期權波動與 Put／Call | Deribit DVOL＋幣本位期權 | OKX 近 30 日 ATM 標記 IV＋幣本位期權 | 每小時；不同波動率定義不得串接 |
| BMNR ETH/BTC/現金/回購 | SEC EDGAR 8-K Exhibit 99.1（CIK 0001829311） | SEC companyfacts 股數 | 每日抓取 |
| 官網 mNAV（僅對照用） | strategy.com | — | 每日，記錄與自算企業價值／BTC 總值差異 |

**審計規則**：每季 10-Q 發布後，用文件數字回校週度累加值；差異 >1% 需在 monitor-log 註記原因。

---

## 三、與觸發框架的接線

| 框架條件 | 改用的自算指標 |
| --- | --- |
| MSTR 普通股估值 | `普通股市值／普通股淨值 ≤ 1.0x` 才能稱折價；仍須資料品質與資本結構覆核 |
| 資本飛輪狀態 | `普通股 Price/NAV ≥ 1.0x` 且 `企業價值/BTC 總值 ≥ 1.0x` 只代表可能仍能溢價融資，不代表普通股便宜 |
| BTC 現貨 regime | 只使用 BTC 五維與 BTC-only 投降／確認條件，不接 MSTR/BMNR 倍率 |
| BTC 長期貨幣化論證 | 黃金、穩定幣／RWA、公開公司持幣、算力及主權信用只作結構背景，不放行短線交易 |
| 「被迫賣幣」判定 | 每週賣幣壓力倍數連續兩週 >2 |
| 儲備健康 | 現金覆蓋月數 < 12 即❌ |
| 優先股體系信任 | STRC 折價 > 5% 期間封鎖 MSTR 2.5x 合約，估值結論降權 |

執行工具見 `calc/mnav_calc.py`。

*非投資建議。所有觸發判定下單前人工核實 EDGAR 原文。*
