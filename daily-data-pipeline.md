# daily-data-pipeline.md — 每日資料管線與驗證代理

## 目的

每天自動更新 MSTR/BTC/BMNR/STRC 相關數據，先由 collector 抓資料，再由獨立 verifier 代理交叉檢查，最後生成前端可讀 JSON 與「每日延伸」三觀點。

## GitHub Actions

- Workflow：`.github/workflows/daily-data.yml`
- 時間：每日 UTC 00:05（台北 08:05）與手動 `workflow_dispatch`
- 權限：`contents: write`，只 commit `data/daily/*.json`

## 腳本分工

1. `scripts/daily_data_pipeline.py`
   - 抓 BTC：CoinGecko + Coinbase。
   - 抓 MSTR/BMNR/STRC：Yahoo Finance chart API。
   - 嘗試抓 SEC submissions；若來源暫時不可用，保留 error observation，不讓 collector 自行宣稱成功。
   - 產出 `data/daily/raw_observations.json`、`latest_snapshot.json`、`database.json`。

2. `scripts/verify_daily_data.py`
   - 這是資料驗證 sub-agent 的可執行版本。
   - 重讀 raw 與 snapshot，不信任 collector 結論。
   - 檢查必要來源、BTC 主備價差、衍生指標合理範圍、SEC 手動覆核警示。
   - 產出 `data/daily/agent_verification_report.json`。

3. `scripts/generate_daily_extensions.py`
   - 結合 `wiki_llm.md` 的概念層與已驗證 snapshot。
   - 產出今天三個延伸觀點。
   - 前端顯示昨天、今天、明天觀察；過期項目移入 `archive`。

## 前端資料

- `daily-extensions.html`：每日三觀點、昨天/今天/明天對比、wiki study inputs。
- `dashboard.html`：新增 daily data JSON 與驗證代理報告入口。
- `index.html`：新增每日延伸入口。

## 驗證政策

- BTC 必須同時有 CoinGecko 與 Coinbase，價差門檻 1.5%。
- MSTR 必須有 Yahoo Finance 價格。
- BMNR/STRC 可缺但降信心。
- SEC/公司公告屬結構性輸入，API 不穩時不自動改 capital structure；保留警示並要求人工覆核。
- verifier fail 時 GitHub Actions 失敗，不 commit 新前端資料。

## 注意

這套管線負責「資料品質與展示」，不是自動下單系統。重大資本結構改變、Strategy 發債/優先股/BTC sale 等事件，仍需人工從 SEC/公司公告覆核後更新 `metrics-spec.md` 與 pipeline manual inputs。
