# 交接：ETF 流量來源掛掉，管線停在 2026-08-11

寫於 2026-08-16。給下一輪接手的 agent。

## 一句話

美國現貨 BTC/ETH ETF 的流量資料來源大批失效，導致 `daily-data.yml` 從
08-11 之後沒有一次成功，下游全部連鎖停擺。守門機制沒壞，它是對的。

## 因果鏈（已驗證）

```
1. ETF 供應商端點失效        blockworks → 404
2. ETF as_of 停在 08-07~08-10
3. FRESHNESS_CONTRACT["etf_source_max_lag_days"] = 5
4. 08-16 時落後 6~9 天 → btc_etf_fresh = False → 流量值設為 None
5. verify_daily_data.py → {"status":"fail","failures":5} → exit 1
6. daily-data.yml 不 commit（最後成功 08-11T01:46）
7. market-universe / market-editorial / deploy 全部連鎖失敗
```

**決定性證據**：`daily-data.yml` 最後成功時間 `08-11T01:46`，與那 12 個
過期來源的 `fetched_at: 2026-08-11T01:46:53` 完全一致。同一次執行留下的
快照，之後再沒被覆蓋。

## 來源實測結果（2026-08-16）

| 來源 | 狀態 | 備註 |
|---|---|---|
| **SoSoValue** | ✅ **可用** | 見下方，唯一通過的 |
| Blockworks `/visualization/814` | ❌ 404 | 端點已不存在 |
| Farside `bitcoin-etf-flow-all-data` | ❌ 403 | 帶瀏覽器 UA 仍 403 |
| The Block `data.tbstat.com` | ❌ 403 | Cloudflare 阻擋 |
| Bitbo | ❌ 404 | |
| CoinGlass `open-api/public/v2` | ❌ 500 | |
| CoinMarketCap etf netflow | ❌ | HTTP 200 但 body 是 `"The system is busy"` |
| Dune public query API | ❌ 401 | 現已強制要求 API key |
| iShares IBIT 官方 | ⚠️ 200 但無資料 | 見下方 |

Claude 與 agy（Gemini 3.7 Flash High）各自獨立驗證，收斂到同一結論。

## 唯一可用的來源

```bash
curl -X POST -H 'Content-Type: application/json' \
  -d '{"type":"us-btc-spot"}' \
  https://api.sosovalue.xyz/openapi/v2/etf/historicalInflowChart
```

- ETH 用 `{"type":"us-eth-spot"}`；`us-eth`、`ETH` 都會回空陣列
- 免費、免 API key
- 欄位：`data[].date`、`data[].totalNetInflow`（USD）
- 實測最新日期 `2026-08-14`（08-15 為週六，故此為正確的最新交易日）
- 實測值合理：ETH 08-14=0、08-13=+6.7M、08-11=-1.8M

## iShares 的陷阱

`btc_etf_ishares_ibit_official_holdings` 現用的 URL：

```
https://www.ishares.com/varnish-api/blk-one01-product-data/product-data/api/v2/get-product-data?...
```

**HTTP 200、397KB，但裡面沒有任何持倉數字**——它回的是頁面 UI 設定檔
（component 標籤、免責聲明、欄位名稱翻譯）。例如：

```
/content/label.navdata.sharesoutstanding: "Shares Outstanding"   ← 只是標籤
/content/holdings.disclaimer: "Holdings are subject to change."
```

不要被 200 騙了。真正的每日持倉在 BlackRock 另一支端點
（通常是 `.ajax?fileType=json` 那種形式）。

## 下一步（單一任務）

**找出 BlackRock IBIT 真正的每日持倉 JSON 端點。**

管線要求至少 2 個獨立來源做 `cross_source_validation`
（見 `daily_data_pipeline.py:build_etf_flow_observations`），現在只有
SoSoValue 一個，不夠。找到 IBIT 官方端點就湊滿兩個。

## 不要做的事

**不要放寬 `etf_source_max_lag_days`。** 那等於自廢守門，會讓過期資料
被當成有效資料發出去。守門機制在這件事上是對的，要修的是上游。

## 備選方案

若第二來源實在找不到，可考慮讓 ETF 欄位標記 `unavailable`，但**不要**
讓它擋住其餘 55 個新鮮來源——目前是整條管線停擺 5 天，其他資料其實是好的。
這需要改 `verify_daily_data.py` 的失敗判定範圍，屬於設計決策，要先問 owner。
