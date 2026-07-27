---
name: market-timescale-intelligence
description: Build, review, or extend the mstr-btc-bottom-report four-horizon market intelligence system. Use when working on daily, weekly, monthly, or quarterly price trends; cross-asset relative strength; multi-source market-data reconciliation; append-only insight history; deterministic exclusive observations; or dashboard copy that must describe market state without producing trading strategy, position sizing, leverage, or target-price instructions.
---

# Four-Horizon Market Intelligence

## Workflow

1. Read `references/data-contract.md` before changing schemas, horizon definitions, or frontend claims.
2. Collect completed bars from two independent providers per asset. Preserve both raw series and identify the canonical provider explicitly.
3. Bind price history, verification, analysis, and market context to the same daily snapshot timestamp.
4. Recompute daily, weekly, monthly, and quarterly features deterministically from canonical bars.
5. Compare the current observation with the latest distinct prior date. Keep same-day reruns as revisions with `supersedes_generated_at`.
6. Produce observations as `claim + evidence + what_changed + falsifier`; never emit an action.
7. Run the collector, both verifiers, deterministic tests, product audit, and browser smoke before publishing.

## Evidence Rules

- Treat source timestamps, completed-bar cutoffs, corporate-action basis, and missing values as first-class data.
- Require two sources for BTC, ETH, MSTR, and BMNR. Degrade isolated non-core fields; fail closed on missing core history or broken lineage.
- Use price, breadth, derivatives, flows, sentiment, network activity, and capital structure as separate lenses. Do not double-count correlated fields as independent confirmation.
- Disable percentiles and statistical language until at least 20 distinct dated observations exist.
- Keep BTC, MSTR, BMNR, and ETH accounting and price interpretations separate.

## Output Boundary

Describe trend, acceleration, volatility, drawdown, range position, relative strength, divergence, and multi-horizon alignment.

Do not output:

- buy, sell, add, reduce, or entry timing;
- leverage or position sizing;
- target prices or promised returns;
- a green execution gate derived from research data.

Use `analysis_only` for readable output and `diagnostics_only` when core evidence fails.

## Commands

```powershell
python scripts/collect_timescale_data.py
python scripts/verify_timescale_data.py
python scripts/generate_timescale_intelligence.py
python scripts/verify_timescale_intelligence.py
python scripts/test_timescale_intelligence.py
python scripts/audit_product_surfaces.py --allow-fail-closed-data
python scripts/smoke_product_surfaces.py
```
