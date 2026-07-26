# frameworks/ — 判斷層的分析框架入口

## 這個 repo 的兩層結構

```
自動化層（確定性）  收集 → 驗證 → 渲染
                    scripts/*.py、.github/workflows/*
                    沒有任何 LLM 呼叫；「AI intelligence」指的是題材，不是方法
判斷層（需要框架）  monitor-log.md、thesis-card-*.md、analysis-*.md
                    institutional-ic-memo-*.md、wiki/
```

框架**只服務判斷層**。自動化層不該被框架影響——它的正確性來自跨源驗證與
fail-closed，不是來自論述品質。

## 什麼時候用哪一個

`manny_skills/` 是 `HsinTiger/manny-li-pro-kb` 的唯讀副本，由上游
`skills/sync-skills.sh` 產生。**不要在這裡編輯**，改上游再同步。

| 觸發情境 | 用 | 為什麼 |
|---|---|---|
| MSTR 的持倉/融資結構出現**質變**（首次淨賣出、ATM 停擺、優先股新發行） | `capital-allocation-engine.md` | 四閘門判定「真引擎／槓桿放大器／熄火中」。MSTR 自稱 BTC 複利引擎，這是檢驗該說法的標準 |
| 要更新**行情觀點**（非行情數據） | `cycle-and-capital-flow.md` | 資金四終點 + 傳染路徑，用來分辨週期波動與結構轉折 |
| 分析單一公司（含 MSTR 的營運面） | `company-teardown.md` | 四段式；若標的是控股/資本配置型，它會叫你改用上面第一個 |

**質變事件才重跑框架，不是每天跑。** 每天跑會讓判定被日常雜訊帶著走，
而日常雜訊已經有 monitor-log 在管。

## 與既有規範的關係

- `AI-VERIFICATION-RULES.md` 管的是**證據**：數據要可溯源、跨源、fail-closed。
- `frameworks/` 管的是**推論**：拿到可信數據之後，怎麼從它得出結論。

兩者不重疊也不互相取代。證據不合格時，正確做法是停在「判定不能」，
而不是用框架把不足的證據推導成結論。

## 硬規則

1. **框架的舉例不是本期事實。** `capital-allocation-engine.md` 裡的 Roper、
   Bending Spoons 只是示範拆法，不可以寫成 MSTR 的數據。
2. **每個觀點都要有證偽條件。** `thesis-card-template.md` 的「證偽條件」欄
   不是選填。沒有可證偽條件的觀點不予採用——這條與框架本身的要求一致。
3. **框架可以判定不適用。** 標的不符前提時，明說換框架，不要硬套。
4. **引用曼報觀點要標來源。** `manny_skills/` 每個檔案都標了它蒸餾自
   上游哪幾篇 `notes/`，那些筆記頂端有原文連結。
