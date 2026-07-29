# Repo 合併與去蕪存菁決策

> 日期：2026-07-07｜2026-07-29 前端決策更新：保留 `mstr-btc-bottom-report` 作為唯一主 repo；`btc-dashboard` 已完成轉址封存，不再維護。主 repo 公開入口再收斂為市場總編。

## 一句話結論

兩個 repo 可以合而為一，而且實際上已經合併完成：`btc-dashboard` 只剩 `index.html` 轉址與 README，GitHub 也已 archive/read-only；後續所有功能、知識庫、儀表板、策略文件都應集中在 `mstr-btc-bottom-report`。

## 保留 / 刪除判斷

| Repo | 狀態 | 決策 | 原因 |
|---|---|---|---|
| `mstr-btc-bottom-report` | 主站、可 push | **保留** | 含 GitHub Pages 前端、dashboard、wiki_llm、策略文件、monitor log、資料歷史 |
| `btc-dashboard` | 已 archive/read-only | **封存，不再更新** | 僅保留轉址；功能已併入主 repo；push 會被 GitHub 拒絕 |

## 不建議做的事

- 不建議真的刪除 `btc-dashboard`：舊連結與 GitHub Pages 入口可能仍有人/瀏覽器書籤使用。
- 不建議雙 repo 同步維護：會造成策略文件、dashboard 與 README 版本分歧。

## 應集中保留在主 repo 的資產

| 類型 | 路徑 |
|---|---|
| 每日跨市場主站 | `market-intelligence.html` |
| 即時市場原始證據 | `market-monitor.html` |
| LLM 投資知識庫前端 | `wiki.html` |
| LLM 可讀知識庫 | `wiki/**`、`wiki/manifest.json` |
| 機構級 IC memo | `institutional-ic-memo-v3.md` |
| 合約波段策略 | `contract-swing-strategy-v2.md` |
| 主交易體系 | `btc-trading-system.md` |
| 監控紀錄 | `monitor-log.md`、`data/daily/timescale_price_history.json`、`data/daily/timescale_intelligence_history.json` |

## 後續操作建議

1. `btc-dashboard` 保持 archived，不再 push。
2. `btc-dashboard` README 與轉址頁已足夠；若要改，只能 unarchive 後更新。
3. 所有新功能只加到 `mstr-btc-bottom-report`。
4. 主站根網址導向市場總編；四週期、時序與洞察 revisions 保留後端，不再拆成重複前端。
5. 舊策略文件只作歷史研究，不屬於 active runtime；目前公開站只做資料分析與市場狀態判讀。
