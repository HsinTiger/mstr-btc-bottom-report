# Reconciliation and Insight Contract

## Reconciliation Manifest

Every material observation must retain:

| Field | Requirement |
|---|---|
| metric | Full Traditional Chinese name and stable machine ID |
| value | Raw value before scoring |
| unit | USD, BTC, ETH, shares, percentage, basis points, or count |
| as_of | Source observation date, not fetch time |
| effective_at | Economic or accounting effective time |
| released_at | Time the source first made the observation public |
| first_seen_at | First successful observation by this system |
| fetched_at | Retrieval time |
| vintage_id | Immutable point-in-time version identifier |
| revision_of | Prior vintage replaced by this observation, if any |
| availability_lag | Delay from release until usable in the pipeline |
| source | URL, filing accession, endpoint, or dataset series |
| source_tier | Official filing/issuer, regulated venue, institutional dataset, or proxy |
| basis | Spot/close/mark; basic/diluted; face/fair/liquidation value; gross/net |
| corporate_action_basis | Split-adjusted, issuance-adjusted, or not applicable |
| verifier | Independent recomputation or comparison used |
| variance | Difference from comparison source in native unit and percentage |
| status | pass, degraded, fail, or unknown |
| issue_class | timing, definition, corporate action, missing, source error, or unknown |
| owner | Parser, collector, verifier, or human-review owner |
| same_origin_group | Common filing or issuer origin shared by derivative artifacts |
| parser_hash | Parser version and retained source-content hash |

Never average two values that use different definitions merely to make them agree.

An issuer filing, its exhibit, press release, and XBRL facts are one origin group, not independent confirmations. Preserve filing accession, table or XBRL tag, original URL, parser version, and content hash.

## Difference Handling

1. **Timing:** Same definition, different reporting cutoff. Preserve both dates and do not call the difference an error.
2. **Definition or basis:** Gross vs net, basic vs diluted, face vs fair value, spot vs mark, or different constituent universes. Keep separate series.
3. **Corporate action:** Split, issuance, conversion, redemption, staking withdrawal, or treasury transfer. Rebuild the denominator or bridge.
4. **Missing observation:** Use a documented fallback only when it measures the same concept. Otherwise publish unknown.
5. **Source error:** Quarantine the source, retain the failed response, and switch only after independent readback.
6. **Unknown:** Block affected conclusions, assign an owner, and age the unresolved item until resolved.

Escalation thresholds must come from each metric's materiality and cadence. Do not reuse illustrative dollar or day thresholds from generic accounting examples.

## Point-in-Time Alignment

- Crypto bars use a declared UTC cutoff and completed bars. Equities use the exchange calendar, official close, and explicit split/dividend adjustment policy.
- Cross-market joins use only observations whose `released_at + availability_lag` was known at that historical instant.
- Event, flow, and filing fields are never forward-filled. A stock field may be carried only under a declared freshness window while retaining its original date and age.
- Derivatives fields declare venue, linear/inverse contract, collateral, mark/index basis, OI notional formula, expiry/delta bucket, annualization, and snapshot time.

## Underlying and Vehicle Separation

### BTC and ETH

Analyze price regime, derivatives, options, flows, on-chain state, network economics, liquidity, credit, and sentiment. Keep correlated observations in the same evidence cluster.

### MSTR

Build common-equity value from BTC fair value, unrestricted cash and other assets, debt, preferred liquidation claims, net deferred tax position, other liabilities, and disclosed obligations. Track BTC per point-in-time fully diluted share and financing changes separately from the stock price.

The fully diluted bridge is period-end basic shares plus RSU/PSU, options/warrants, convertibles, convertible preferred, and subsequent ATM issuance, less repurchases. A quarterly weighted-average diluted EPS denominator is not a period-end share count.

For every debt or preferred instrument retain face, carrying, market, and liquidation values; accrued interest/dividend; conversion, call/put, maturity, and seniority. Never deduct a claim and add its conversion shares in the same scenario. Separate GAAP net DTL/DTA, valuation allowance, tax basis, and economic liquidation-tax sensitivity.

### BMNR

Distinguish reported gross ETH treasury from common-equity net value. Track ETH per fully diluted share, liabilities, staking status, custody, validator/slashing exposure, operating costs, and financing dilution. Do not infer net NAV when liabilities or diluted shares are unresolved.

## Return Decomposition

For a DAT vehicle, first use the exact identity where `P` is share price, `N` is common-equity NAV per diluted share, and `m=P/N`:

```text
P_t / P_(t-1) = [N_t / N_(t-1)] * [m_t / m_(t-1)]
```

Then reconcile the change in `N` with a sequential dollar bridge for underlying revaluation, net treasury change, non-crypto assets/liabilities, financing or staking carry, operations, and diluted-share change. Keep interaction and residual terms visible. A large residual is a research question, not evidence that the model is correct.

## Exclusive Indicator Admission

A proposed indicator enters the daily product only after all checks pass:

1. Formula and economic mechanism are explicit.
2. Every input has a source, date, unit, basis, freshness window, and fallback policy.
3. Missing inputs produce unknown or fail, never zero.
4. Correlated inputs are grouped before resonance votes are counted.
5. Revision behavior and look-ahead risks are tested.
6. Walk-forward evaluation reports coverage, calibration, false positives, and regime stability.
7. The indicator adds information beyond simpler public measures.

Walk-forward claims require a frozen label, prediction horizon, tolerance window, overlapping-event rule, baseline, purged rolling or expanding split, embargo, threshold, refit cadence, confidence interval, class-imbalance treatment, and multiple-testing policy. The evaluation must replay point-in-time vintages.

## Insight Template

Use this order:

1. **結論:** One sentence.
2. **關鍵數字:** One raw or transparently calculated value.
3. **今日變化:** Difference from the latest distinct prior date.
4. **機制:** Why the relationship could exist.
5. **多維證據:** At least three independent clusters when claiming resonance.
6. **反方解讀:** Strongest plausible alternative.
7. **證偽:** Observable condition that would invalidate the hypothesis.
8. **限制:** Missing data, proxy use, sample size, and model status.

Do not turn confidence, materiality, or resonance into an execution instruction.
