# BTC／ETH／MSTR／BMNR 機構級分析計畫 v2

> 狀態：研究方法升級計畫。此文件不產生買賣、槓桿、部位或單一目標價指令。

## 一句話目標

先把資料、日期、定義與資本結構對帳，再分開判斷 BTC／ETH 底層體制和 MSTR／BMNR 上市載具，最後只發布可重算、可證偽、能追蹤修訂的洞察。

## 目前已證明與尚未完成

### 已證明（只限目前 artifact 的資料與計算完整性）

- BTC、ETH、MSTR、BMNR 已有多來源完成日 K、四週期分析、獨立 verifier 與 append-only revisions；證據為 `data/daily/timescale_data_verification.json`，不延伸為預測有效性或會計級資本結構驗證。
- 市場總編已整合八個研究桌，並保留來源日期、反方解讀、二階影響與證偽條件。
- 跨資產市場資料已有來源 failover；核心資料或 lineage 失敗時會 fail closed。
- MSTR 已收集官方持幣、SEC 資本結構、優先股與部分固定義務欄位，但尚未構成 accounting-grade common-equity bridge；BMNR 明示 gross treasury 不等於 common-equity net NAV。

### 尚未證明

- 尚未完成所有獨家指標的 walk-forward 校準，不能宣稱穩定預測底部、勝率或超額報酬。
- 現有歷史樣本不足以執行可信 walk-forward；第三階段維持 `BLOCKED`，不得用同一批資料同時探索、調參與驗證。
- BMNR 的完整負債、充分稀釋股數、淨質押收益與 validator 風險仍需更完整的官方揭露橋接。
- ETF、DAT、鏈上、衍生品和宏觀資料的頻率不同，不能合成成一個假裝精準的總分。
- MSTR／BMNR 的估值情境仍是敏感度分析，不是價格預測。

## 四個研究帳本

| 帳本 | 核心問題 | 不可混入 |
|---|---|---|
| BTC 底層體制 | 貨幣採用、鏈上成本基礎、供給結構、槓桿、流向與流動性是否共振？ | MSTR 股價或融資飛輪 |
| ETH 底層體制 | 費用、發行、質押、穩定幣／RWA 結算、槓桿與機構流向是否改善？ | BMNR 股價或公司敘事 |
| MSTR 上市載具 | BTC 每股含量、普通股淨值、融資成本、優先求償、稀釋與反身性如何變化？ | 把 BTC NAV 當普通股 NAV |
| BMNR 上市載具 | ETH 每股含量、gross-to-net bridge、質押品質、負債、稀釋與營運風險如何變化？ | 把 gross ETH treasury 當普通股淨值 |

## 分析層級

### 1. 資料對帳層

每個重要欄位保存來源、`as_of`、`effective_at`、`released_at`、`first_seen_at`、`fetched_at`、`vintage_id`、`revision_of`、availability lag、單位、幣別、會計／市場基礎、公司行動基礎、比較來源、差異、狀態與 owner。申報資料另保存 filing accession、表格／XBRL tag、原文 URL、parser 版本與內容 hash。

差異固定分成：時間差、定義／基礎差、公司行動、缺值、來源錯誤、未知。未知差異不帶入結論，也不以兩來源平均值掩蓋。

同一 filing、8-K exhibit、公司新聞稿及其 XBRL 只能歸為同一 `same_origin_group`，不得冒充多個獨立來源。

### 1.1 時間序列對齊契約

- 加密現貨與永續合約固定以 UTC 日界及已完成 bar 計算；股票固定交易所日曆、官方收盤與拆股／股息調整政策。
- 每個衍生品欄位固定 venue、linear／inverse、抵押幣別、mark／index、OI 名目值算法、到期與 delta bucket、年化公式及快照時間。
- 股票與加密跨市場比較只做 point-in-time as-of join；以 `released_at + availability_lag` 判斷當時是否可見，禁止用日後修訂值回填當時決策面。
- 缺值不得 forward-fill 事件、流量或申報欄位；僅可對明確允許的存量欄位使用有時限的 carry-forward，並保留原始日期與 age。

### 2. 市場體制層

分開呈現，不合成黑箱總分：

- 價格與趨勢：報酬、加速度、波動率、回撤、區間位置、跨資產廣度。
- 衍生品與期權：資金費率、期限基差、未平倉量、清算、隱含波動率、偏度與期限結構。
- 機構流向：BTC／ETH ETF、DAT 持倉變化、價格對可觀測流量的吸收反應。
- 鏈上與網路：BTC 成本基礎與持有者行為；ETH 發行、費用、質押、驗證者與結算活動。
- 宏觀與信用：Fed 淨流動性、銀行準備金、M2、美元、實質利率、殖利率曲線、信用利差、原油與美股。
- 情緒與定位：只作補充，不以單一問卷或社群熱度決定體制。

### 3. 上市載具層

MSTR 與 BMNR 每次更新都要重建：

1. 底層加密資產公允價值。
2. 現金及其他可辨識資產。
3. 逐項債務與優先股的 face、carrying、market、liquidation、應計利息／股息、轉換、call／put、到期與順位；轉換情境不得同時扣求償又加入轉換股數。
4. point-in-time fully diluted shares bridge：basic shares＋RSU／PSU＋options／warrants＋convertibles＋convertible preferred＋後續 ATM－回購；季度加權平均 diluted EPS denominator 不得代替期末 fully diluted shares。
5. 固定現金義務、到期結構、利率／股息與可用現金。
6. GAAP net DTL／DTA、valuation allowance、tax basis 與 economic liquidation-tax sensitivity 分層呈現；DTA 與 DTL 必須依正負號正確加減。
7. restricted cash、encumbered assets、NCI、應計股息、其他負債與表外承諾。
8. 每股含幣量、融資新增資產、稀釋與普通股淨值橋接。

### 4. 相對價值與情境層

先以恆等式拆解價格與估值倍數：若 `P_t` 為股價、`N_t` 為每股普通股淨值、`m_t=P_t/N_t`，則 `P_t/P_{t-1}=(N_t/N_{t-1})×(m_t/m_{t-1})`，或使用 log-return 精確相加。再以順序式 dollar bridge 拆解 `N_t` 的底層資產重估、淨新增資產、非加密資產／負債、融資／質押 carry、營運與 fully diluted share 變化；交互項與殘差必須獨立顯示，不得重複歸因。

以 bear／base／bull 敏感度矩陣呈現 BTC／ETH 價格、折溢價、稀釋、負債與稅務假設；不發布單點目標價。

## 優先建立的獨家指標

| 完整名稱 | 核心公式／用途 | 目前狀態 |
|---|---|---|
| 普通股市值對可歸屬普通股加密淨值倍數 | 普通股市值 ÷（加密資產＋現金＋其他資產－債務－優先股清算求償－其他負債－GAAP net DTL＋可認列 DTA）；分母小於或等於零時顯示 `N/M`，另做 liquidation-tax sensitivity | MSTR 部分可算；BMNR 待完整負債橋接 |
| 每股充分稀釋比特幣／以太幣含量變化 | treasury units ÷ point-in-time fully diluted shares，並追蹤融資前後變化 | MSTR 分母待重建；BMNR 待 diluted shares 品質提升 |
| 固定現金義務覆蓋月數 | 未受限制且可自由運用現金 ÷ 月化利息、應付優先股股息與營運現金消耗 | MSTR 可深化；BMNR 待揭露 |
| 每股含幣量增厚／稀釋率 | `(U1/S1)/(U0/S0)-1`；另列淨募資、購幣、費用、償債及保留現金橋接 | 計畫中；需處理 point-in-time 分母與公司行動 |
| 機構流入價格吸收背離 | ETF／DAT 可觀測流入方向與同期間價格反應是否相反 | 已有原型；需 walk-forward 校準 |
| 衍生品擁擠與期權壓力差 | funding、basis、OI、IV、put／call OI 已部分具備；skew、完整期限曲面與清算序列補齊後才可判斷多維共振 | 部分已有；禁止黑箱加權 |
| 三速美元流動性共振 | M2 慢背景、銀行準備金金融脈衝、Fed 淨流動性政策脈衝各一票 | 已上線 |
| BMNR 淨質押收益品質 | `(rewards＋MEV－validator/custody fees－slashing－直接營運成本) ÷ 平均實際質押 ETH`，另列 legal title、withdrawal authority、集中度、解押期、LST／restaking 與資產負擔 | 未完成；不得以標稱收益代替 |

## 洞察發布契約

每個重要觀點固定回答：

1. 結論是什麼？
2. 今天唯一最重要的數字是什麼？
3. 相較上一個相異日期改變了什麼？
4. 經濟機制為何可能成立？
5. 至少三個帶固定 `cluster_id` 的獨立證據群是否共振？每群只有一票；MSTR／BMNR 股價不得參與 BTC／ETH 底層體制投票。
6. 哪些是領先、同步、落後證據？
7. 最強反方解讀是什麼？
8. 什麼可觀測條件會推翻結論？
9. 哪些欄位仍未知、使用代理或樣本不足？

## 驗證標準

- 資料品質：來源可用率、跨來源差異、時間與定義一致、fallback readback、修訂幅度。
- 指標品質：有效覆蓋率、缺值率、方向穩定度、與簡單基準的增量資訊。
- 預警品質：每個模型先凍結 bottom／regime-transition label、預測 horizon、容忍窗、重疊事件規則與 baseline，再報告 walk-forward precision、recall、false-positive rate、平均領先期與不同體制穩定度。
- 機率品質：若輸出機率，報告 Brier score 與 calibration；否則不得用百分比信心包裝主觀判斷。
- 研究治理：每個指標都有公式、來源、freshness、fallback、修訂政策、owner、證偽與退場條件。
- 防止過度擬合：只使用 point-in-time vintage；採 purged rolling／expanding walk-forward、embargo、門檻凍結、固定重估頻率、bootstrap confidence interval、類別不平衡處理及 multiple-testing policy；不因單一歷史底部調參。
- 最低樣本：一般日頻指標至少 250 個 distinct point-in-time 日期與 30 個預先定義事件才可做第一版估計；週期底部模型另需至少兩個完整市場週期，仍不得宣稱穩定機率。未達門檻一律標示 `experimental` 或 `BLOCKED`。

## 交付節奏

| 頻率 | 內容 |
|---|---|
| 每小時 | 現貨、衍生品、熱門資產與來源健康；ETF、DAT 與申報資料依官方發布節奏更新，不偽裝成小時級資料 |
| 每日 | 市場總編結論、關鍵數字、一行含意、今日變化、證偽與資料狀態 |
| 每週 | 領先／落後證據轉換、來源差異 aging、ETF／DAT 與資本結構變化 |
| 每月 | 多週期體制、情境敏感度、獨家指標校準與 false-positive review |
| 申報事件 | 重建 MSTR／BMNR 資本結構、fully diluted shares 與 common-equity bridge |

## 實作順序

### 第一階段：資料可信度（優先）

- 建立 material field reconciliation manifest 與 unresolved-difference aging。
- 建立 point-in-time raw vintage store，保存 release／first-seen／revision lineage，並完成可重播的歷史回填。
- 對 MSTR／BMNR 所有估值欄位加上 unit、basis、as_of、source tier 與 verifier。
- 將定義差異與抓取失敗分開；定義不同不得標為來源不一致。
- **完成定義：** schema 驗證、兩個同定義來源或官方原文＋獨立重算、mutation test、失敗 fixture、owner 與公開 diagnostics 全數通過。

### 第二階段：載具報酬拆解

- 上線 MSTR common-equity bridge 與 fully diluted BTC per share。
- BMNR 先發布 gross-to-net missing bridge，不完整時維持 unknown。
- 建立 underlying／premium／dilution／carry／residual 的歷史序列。
- **完成定義：** point-in-time FD bridge、逐項 claim matrix、稅務正負號、轉換雙算防護、恆等式 reconciliation 與 interaction／residual test 全數通過。

### 第三階段：指標校準（目前 `BLOCKED`）

- 對機構流入吸收、衍生品擁擠、鏈上成本基礎與流動性共振做 walk-forward 測試。
- 報告 coverage、false positives、lead time、regime stability；不足就保留 experimental。
- **解除條件：** 達成預先登記的日期、事件與 regime coverage；固定 label、baseline、split、embargo、threshold 與 multiple-testing policy，並由獨立 verifier 重播。

### 第四階段：前端精簡

- 首頁只顯示通過驗證的結論、單一關鍵數字與一行含意。
- 詳細公式、來源、情境與歷史 revisions 留在市場總編收合區、知識庫與後端 JSON。
- calibration 未通過時明示 `experimental`；核心欄位缺失時顯示 `unknown`，不得產生方向性替代文案。
- **完成定義：** 桌面／手機瀏覽器 smoke、鍵盤操作、零 overflow、資料日期與後端一致、退化狀態可讀及 production hash readback 全數通過。

## Skill 使用

- 已安裝並固定 Anthropic `reconciliation` skill commit `2d6f7e22dd25593f0f748010430ef86f19659735`，只採用對帳、差異分類、root-cause 與 reviewer 方法。
- 專案方法由 `skills/market-timescale-intelligence/SKILL.md` 控制。
- Jupyter Notebook、Spreadsheets 與 Visualize 用於可重算分析和視覺化；不得繞過 verifier 或把探索結果直接發布為已證明結論。
