# Data Contract

## Method provenance

This workflow adapts the evidence-ledger, diff-first, deterministic-gate, two-signal resonance, and append-only revision practices documented by `HsinTiger/skills-radar` on 2026-07-27. No third-party finance skill is installed: that radar marked the candidate list `PREVIEW_STALE_CORPUS` and required pin, license, source/security review, offline canary, and owner approval before adoption.

## Lineage

All derived artifacts must bind to `data/daily/latest_snapshot.json.generated_at`.

| Artifact | Purpose | Required binding |
|---|---|---|
| `timescale_price_history.json` | Raw completed daily bars from two providers | `snapshot_generated_at` |
| `timescale_data_verification.json` | Cross-source structural and numerical checks | `history_generated_at`, `snapshot_generated_at` |
| `timescale_intelligence.json` | Four-horizon analysis and exclusive observations | snapshot, price history, market universe |
| `timescale_intelligence_history.json` | Append-only compact observation ledger | latest item equals current analysis |
| `timescale_intelligence_verification.json` | Independent math, scope, lineage, and history audit | `analysis_generated_at` |

## Horizon Definitions

| Horizon | Return | Fast trend | Slow trend | Volatility | Range |
|---|---:|---:|---:|---:|---:|
| Daily | 1 bar | 5 bars | 20 bars | 20 bars | 20 bars |
| Weekly | 5 bars | 10 bars | 30 bars | 30 bars | 60 bars |
| Monthly | 21 bars | 21 bars | 63 bars | 63 bars | 126 bars |
| Quarterly | 63 bars | 63 bars | 126 bars | 126 bars | 252 bars |

Use completed daily bars. These are analysis windows, not execution instructions.

## Trend State

- `上升趨勢`: close > fast average > slow average and horizon return > 0.
- `下降趨勢`: close < fast average < slow average and horizon return < 0.
- `方向切換`: current and previous equal-window returns have opposite signs without ordered averages.
- `震盪分歧`: remaining known combinations.
- `資料不足`: required values are absent.

## Cross-Source Gates

- Median overlapping close gap <= 1%.
- 95th-percentile close gap <= 3%.
- Daily/weekly/monthly/quarterly return gap <= 5 percentage points.
- Latest provider dates differ by no more than five days.
- Current verified price and latest completed close differ by no more than 25%; above 10% is degraded.

## Historical Claims

Store every run. Same-day reruns increment `revision` and set `supersedes_generated_at`. Use the latest distinct prior date for `what_changed`. Require 20 distinct dates before publishing empirical percentiles.
