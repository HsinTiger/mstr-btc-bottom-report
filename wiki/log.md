# Wiki 變更日誌（追加式）

格式：`## [YYYY-MM-DD] op | 標題`。查詢：`grep "^## \[" log.md | tail`。

## [2026-07-15] ingest | 加密招財貓新片上新（WebSearch索引偵測，RSS封鎖中）
加密招財貓疑似發布今日新片「2026/07/15 (三) IBM 暴跌 25% 史上最慘，AI 正在吞噬傳統科技業？」（https://youtu.be/GV3gCpFUQcM），內容聚焦IBM財報暴跌25%事件及AI對傳統科技業的衝擊；YouTube RSS持續403封鎖，本偵測源自WebSearch索引，頻道確認度中等。BTC當日因June CPI軟化反彈+4.9%至$65,030，重回[[five-dimension-model]] 200DMA上方；BTC ETF則鉅額出逃-$424.7M。[[kol-roster]]

## [2026-06-30] alert | MSTR mNAV由≤0.6x反彈至~0.62x，第1等份④觸發條件暫解除
MSTR今日大漲+8.63%（自52週低點$81.81反彈至$89.07），市值回升至~$31.74B，估算mNAV ~0.62x，穿回[[two-tranche-plan]]第1等份④的0.6x門檻以上；該條件由「連續3日觸發（6/27–29）」轉為今日暫解除。BTC ~$60,000，整體綜合溫度-4.1（較昨日-4.6回升）。BMNR ETH增至5,700,040（+27K），新加入Russell 1000；staking量增至4,879,157 ETH。**框架行動：等待，四個第1等份條件均未達標；若MSTR回落使mNAV重跌至≤0.6x則條件再度成立。**

## [2026-06-28] alert | 第1等份條件④確認觸發：MSTR mNAV(basic) 連續2日<0.6x
BTC/ETH自昨日恐慌低點反彈+4.18%、F&G由極度恐懼13回升至約17–39區間，整體綜合溫度由−5.3回升至−4.5，距[[two-tranche-plan]]第1等份③門檻(−6)拉開距離。但MSTR mNAV(basic) 0.58x經CoinDesk 6/27報導重申、連續≥2個交易日由多源一致確認低於[[two-tranche-plan]]④的0.6x扳機，信心由「疑似待核實」提升至「中高確信」，**判定為第1等份(50%)已觸發**（單一維度財庫便宜，非五維共振投降）。MSTR本期無新增強制賣幣，6月初已反向買回1,550 BTC。更新 [[mstr]]、[[mnav-reflexivity]]、[[two-tranche-plan]]。

## [2026-06-27] alert | BTC破200DMA創年內新低、MSTR mNAV疑跌破0.6x扳機
BTC跌破$60k、200DMA、年內新低$58,115，距ATH回撤52.3%；綜合溫度−5.3創本輪最深、距[[two-tranche-plan]]第1等份③門檻(−6)僅差0.7格。MSTR股價跌至兩年低$85.33，現價首度低於其BTC持倉均成本$66,384.56（估浮虧$10.6B），多源/自算mNAV落在0.58–0.63x，疑似首次觸及[[two-tranche-plan]]④的0.6x扳機，但因 WebFetch 持續403、來源口徑衝突，信心中低，標記待人工核實。更新 [[mstr]]、[[mnav-reflexivity]]、[[target-price]]。

## [2026-06-14] init | 建立 LLM Wiki（Karpathy 模式）
建立 [[CLAUDE]] schema、[[overview]]、概念頁（[[five-dimension-model]]、[[cycle-diminishing-returns]]、[[mnav-reflexivity]]、[[coin-per-share-accretion]]、[[target-price]]、[[two-tranche-plan]]）、實體頁（[[mstr]]、[[bmnr]]、[[strc-preferred]]、[[kol-roster]]）、來源頁（[[data-feeds]]）。建 wiki.html graph view 前端。

## [2026-06-14] ingest | KOL 字幕 ×3（招財貓/直男說/區塊鏈日報）
納入 raw/kol-subtitles/。三家抄底派底部共識 $46–56k，與 [[target-price]] $40–52k 重疊。更新 [[kol-roster]]。

## [2026-06-14] ingest | 五維監控升級 + 歷史趨勢
監控加 5 KOL RSS 偵測、每日 append data/history.json；前端加「歷史趨勢」graph。更新 [[data-feeds]]、[[five-dimension-model]]。
