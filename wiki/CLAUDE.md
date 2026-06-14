# 幣圈投資 LLM Wiki — Schema / 維護規範

本目錄是依 [Karpathy LLM Wiki 模式](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) 維護的**幣圈投資知識庫**。LLM 負責整理、交叉引用、保持一致；人類負責提供來源與提問。

## 三層架構
1. **raw/**（不可變來源）：原始資料——data snapshot、monitor-log、KOL 字幕、官方 PR、截圖。LLM 不改寫，只引用。
2. **wiki/**（LLM 維護）：markdown 知識頁，含摘要、實體頁、概念頁、比較、總述，彼此用 `[[wikilink]]` 交叉引用。
3. **本檔（schema）**：定義結構、關係詞彙、ingest/query/lint 流程。

## 目錄結構
```
wiki/
├── CLAUDE.md        # 本檔：schema
├── index.md         # 目錄（每頁一行+摘要）
├── log.md           # 追加式時間軸（## [YYYY-MM-DD] op | 標題）
├── overview.md      # 跨來源總述（投資論點現況）
├── manifest.json    # 給前端 graph view 的頁面清單
├── concepts/        # 概念/方法（五維模型、週期遞減、反身性…）
├── entities/        # 標的/人物（MSTR、BMNR、STRC、KOL…）
└── sources/         # 每個資料來源的摘要頁
../raw/              # 不可變原始來源（snapshot、字幕、log）
```

## Markdown 慣例
- 每頁開頭 YAML frontmatter：`title`、`type`(concept|entity|source|overview)、`tags`、`updated`、`confidence`(high|med|low)。
- 用 `[[slug]]` 連結其他頁（slug = 檔名去 .md）。連到尚未建立的頁也可以，代表待補。
- 數字一律標**來源平台 + 日期**；抓不到寫「未取得」，**絕不編造**。

## 關係詞彙（描述頁與頁/主張與主張的關係）
`supports`（佐證）、`contradicts`（矛盾，**保留兩方、標記待人工裁決**）、`extends`（延伸）、`supersedes`（取代舊主張）、`was-response-to`（回應某觀點，如我們 vs KOL）。

## 三個核心操作
- **Ingest（納入）**：新來源放 raw/ → 讀取、討論重點 → 寫 `sources/` 摘要頁 → 更新相關 entity/concept 頁 → 更新 [[index]] → 在 [[log]] 追加一行。
- **Query（查詢）**：先看 [[index]] → 讀相關頁 → 綜合作答並附引用 → 好答案可回填成新頁。
- **Lint（健檢）**：定期找矛盾、過時主張、孤兒頁（無入鏈）、缺交叉引用、缺頁的重要概念、數據缺口。

## 信心分級（決定可否自動更新）
- **high**：官方 SEC/PR、鏈上權威平台多源一致 → 直接更新。
- **med**：單一可信來源 → 更新但標註。
- **low / 矛盾**：標 `contradicts` 旗標、留待人工裁決，不覆蓋舊主張。

## 投資領域本體（ontology）
- 標的：[[mstr]]、[[bmnr]]、BTC、ETH、[[strc-preferred]]
- 方法：[[five-dimension-model]]、[[two-tranche-plan]]、[[target-price]]
- 機制：[[mnav-reflexivity]]、[[coin-per-share-accretion]]、[[cycle-diminishing-returns]]
- 外部觀點：[[kol-roster]]（與我們的質疑用 `was-response-to`）
- 數據流：[[data-feeds]]（五維權威平台 + 每日監控）

## 與前端的關係
- 主決策儀表：`../index.html`（八分頁：總覽/歷史趨勢/五維/KOL）。
- 本 Wiki 的瀏覽前端：`../wiki.html`（側欄＋內容＋關係圖 graph view），讀 `manifest.json` 與各 .md。
