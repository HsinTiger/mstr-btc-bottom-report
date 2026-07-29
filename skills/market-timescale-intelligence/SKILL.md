---
name: market-timescale-intelligence
description: Build, review, or extend institutional BTC, ETH, MSTR, and BMNR research in mstr-btc-bottom-report. Use for multi-source reconciliation, daily/weekly/monthly/quarterly regime analysis, BTC/ETH underlying-vs-DAT vehicle separation, MSTR/BMNR capital-structure and per-share treasury analysis, append-only insight history, falsifiable cross-market observations, model calibration, or dashboard copy that must describe market state without producing trading strategy, position sizing, leverage, or target-price instructions.
---

# Institutional Crypto and DAT Intelligence

## Workflow

1. Read `references/data-contract.md` before changing schemas, horizon definitions, or frontend claims.
2. Read `references/reconciliation-and-insight.md` before adding company fundamentals, valuation, exclusive indicators, or model-quality claims.
3. Reconcile every material field across source, date, unit, currency, accounting basis, corporate-action basis, and definition before analysis.
4. Collect completed bars from two independent providers per asset. Preserve both raw series and identify the canonical provider explicitly.
5. Bind price history, verification, analysis, company filings, and market context to explicit source timestamps.
6. Analyze BTC and ETH as underlying networks; analyze MSTR and BMNR as separate financing and capital-structure vehicles.
7. Recompute daily, weekly, monthly, and quarterly features deterministically from canonical bars.
8. Compare the current observation with the latest distinct prior date. Keep same-day reruns as revisions with `supersedes_generated_at`.
9. Produce observations as `claim + evidence + mechanism + what_changed + falsifier`; never emit an action.
10. Run collectors, independent verifiers, deterministic and adversarial tests, product audit, and browser smoke before publishing.

## Research Stack

Keep these layers separate and show disagreements instead of forcing one score:

1. **Underlying regime:** price path, drawdown, volatility, breadth, derivatives, options, flows, on-chain activity, macro liquidity, credit, and sentiment.
2. **Network economics:** BTC security and monetary adoption; ETH fees, issuance, staking, validator concentration, and stablecoin/RWA settlement activity.
3. **Vehicle fundamentals:** treasury units, fully diluted shares, debt, preferred claims, cash, taxes, fixed obligations, issuance, custody, and operating cash flow.
4. **Relative value:** underlying return, vehicle premium/discount, dilution/accretion, financing carry, and operating contribution as separate return components.
5. **Evidence synthesis:** common view, non-consensus hypothesis, mechanism, second-order effect, leading evidence, lagging confirmation, and falsifier.

Do not let MSTR or BMNR price action change the BTC or ETH underlying regime. Do not call gross treasury assets common-equity NAV.

## Evidence Rules

- Treat source timestamps, completed-bar cutoffs, corporate-action basis, and missing values as first-class data.
- Require two sources for BTC, ETH, MSTR, and BMNR. Degrade isolated non-core fields; fail closed on missing core history or broken lineage.
- Use price, breadth, derivatives, flows, sentiment, network activity, and capital structure as separate lenses. Do not double-count correlated fields as independent confirmation.
- Classify unresolved differences as timing, definition/basis, corporate action, missing observation, source error, or unknown. Track owner, age, and resolution.
- Use primary filings and issuer disclosures for capital structure; use market-data providers only for prices and clearly named proxies.
- Preserve point-in-time vintages with release, first-seen, revision, parser, and content-hash lineage. Never backfill a historical decision surface with a later revision.
- Build fully diluted shares from point-in-time components. Never use a quarterly weighted-average diluted EPS denominator as period-end shares.
- Model each debt and preferred claim in conversion and non-conversion scenarios without deducting the claim and adding conversion shares in the same state.
- Disable percentiles and statistical language until at least 20 distinct dated observations exist.
- Keep BTC, MSTR, BMNR, and ETH accounting and price interpretations separate.

## Insight Quality

- Require at least three genuinely independent evidence clusters for a multi-dimensional resonance claim.
- Give each stable `cluster_id` one vote. MSTR or BMNR price cannot vote in the BTC or ETH underlying regime.
- Label evidence as leading, coincident, or lagging; a lagging metric may confirm but cannot be presented as an early signal.
- Show raw values and dates before interpretation. Never hide uncertainty inside a weighted composite.
- Decompose every MSTR/BMNR observation into underlying move, premium/discount change, treasury-per-diluted-share change, financing burden, and company-specific risk when fields exist.
- Treat any proposed indicator as experimental until its formula, source availability, missing-data behavior, revision policy, and walk-forward evaluation are documented.
- Measure calibration, false positives, stability across regimes, and revision sensitivity; do not validate a signal only with cherry-picked historical returns.

## Cadence

- **Daily:** one conclusion, one key number, one-line meaning, source health, and next falsifier per important dimension.
- **Weekly:** regime changes, reconciliation exceptions, MSTR/BMNR financing changes, and leading-vs-lagging evidence review.
- **Monthly:** scenario sensitivities, capital-stack refresh, indicator calibration, false-positive review, and retired/added signal decisions.
- **Quarterly or filing-driven:** rebuild MSTR/BMNR common-equity bridge from official filings before reusing valuation conclusions.

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
