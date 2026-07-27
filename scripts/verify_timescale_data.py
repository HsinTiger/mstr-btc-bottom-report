#!/usr/bin/env python3
"""Independently verify multi-source daily-bar history before analysis."""

from __future__ import annotations

import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "daily"
HISTORY_PATH = DATA_DIR / "timescale_price_history.json"
SNAPSHOT_PATH = DATA_DIR / "latest_snapshot.json"
OUTPUT_PATH = DATA_DIR / "timescale_data_verification.json"

MINIMUM_BARS = {"BTC": 300, "ETH": 300, "MSTR": 300, "BMNR": 200, "STRC": 120}
CORE_ASSETS = {"BTC", "ETH", "MSTR", "BMNR"}
SNAPSHOT_PRICE_KEYS = {
    "BTC": "btc_usd",
    "ETH": "eth_usd",
    "MSTR": "mstr_usd",
    "BMNR": "bmnr_usd",
    "STRC": "strc_usd",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def number(value: Any) -> float | None:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = round((len(ordered) - 1) * probability)
    return ordered[index]


def period_return(rows: list[dict[str, Any]], bars: int) -> float | None:
    if len(rows) <= bars:
        return None
    current = number(rows[-1].get("close"))
    previous = number(rows[-bars - 1].get("close"))
    return current / previous - 1 if current is not None and previous not in (None, 0) else None


def verify_rows(rows: list[dict[str, Any]]) -> list[str]:
    failures: list[str] = []
    dates = [str(row.get("date") or "") for row in rows]
    if dates != sorted(dates) or len(dates) != len(set(dates)):
        failures.append("dates are not strictly increasing and unique")
    today = datetime.now(timezone.utc).date().isoformat()
    if any(not date or date >= today for date in dates):
        failures.append("series contains incomplete or future bars")
    for row in rows:
        close = number(row.get("close"))
        high = number(row.get("high"))
        low = number(row.get("low"))
        if close is None or close <= 0:
            failures.append("series contains non-positive close")
            break
        if high is not None and low is not None and (high < low or close > high * 1.001 or close < low * 0.999):
            failures.append("OHLC bounds are inconsistent")
            break
    return failures


def compare_sources(primary: dict[str, Any], secondary: dict[str, Any]) -> dict[str, Any]:
    primary_rows = primary.get("rows") or []
    secondary_rows = secondary.get("rows") or []
    primary_by_date = {row["date"]: number(row.get("close")) for row in primary_rows}
    secondary_by_date = {row["date"]: number(row.get("close")) for row in secondary_rows}
    overlap_dates = sorted(set(primary_by_date) & set(secondary_by_date))
    gaps = []
    for date in overlap_dates:
        first = primary_by_date[date]
        second = secondary_by_date[date]
        if first is not None and second not in (None, 0):
            gaps.append(abs(first - second) / ((abs(first) + abs(second)) / 2))
    return_gaps: dict[str, float | None] = {}
    for bars in (1, 5, 21, 63):
        first_return = period_return(primary_rows, bars)
        second_return = period_return(secondary_rows, bars)
        return_gaps[str(bars)] = abs(first_return - second_return) if first_return is not None and second_return is not None else None
    latest_primary = datetime.fromisoformat(primary_rows[-1]["date"]).date() if primary_rows else None
    latest_secondary = datetime.fromisoformat(secondary_rows[-1]["date"]).date() if secondary_rows else None
    return {
        "overlap_bars": len(overlap_dates),
        "median_close_gap": statistics.median(gaps) if gaps else None,
        "p95_close_gap": percentile(gaps, 0.95),
        "maximum_close_gap": max(gaps) if gaps else None,
        "period_return_gaps": return_gaps,
        "latest_date_gap_days": abs((latest_primary - latest_secondary).days) if latest_primary and latest_secondary else None,
    }


def verify() -> dict[str, Any]:
    history = load_json(HISTORY_PATH)
    snapshot = load_json(SNAPSHOT_PATH)
    failures: list[str] = []
    degradations: list[str] = []
    checks: list[dict[str, Any]] = []
    if history.get("schema") != 1:
        failures.append("history schema is not 1")
    if history.get("snapshot_generated_at") != snapshot.get("generated_at"):
        failures.append("history is not bound to the current daily snapshot")
    if history.get("source_batch_id") != snapshot.get("batch_id"):
        failures.append("history source batch does not match the current daily snapshot")
    snapshot_prices = snapshot.get("metrics", {}).get("prices", {})
    for symbol, minimum_bars in MINIMUM_BARS.items():
        asset = history.get("assets", {}).get(symbol) or {}
        sources = asset.get("sources") or {}
        canonical_provider = asset.get("canonical_provider")
        canonical = sources.get(canonical_provider) if canonical_provider else None
        asset_failures: list[str] = []
        asset_degradations: list[str] = []
        if not canonical:
            asset_failures.append("canonical series missing")
        else:
            row_failures = verify_rows(canonical.get("rows") or [])
            asset_failures.extend(row_failures)
            if len(canonical.get("rows") or []) < minimum_bars:
                asset_failures.append(f"canonical bars below {minimum_bars}")
        comparison = None
        comparison_provider = None
        comparisons: dict[str, Any] = {}
        secondary_providers = [provider for provider in sources if provider != canonical_provider]
        if not secondary_providers:
            message = "independent secondary series missing"
            (asset_failures if symbol in CORE_ASSETS else asset_degradations).append(message)
        elif canonical:
            passing_comparisons: list[tuple[str, dict[str, Any]]] = []
            for provider in secondary_providers:
                secondary = sources[provider]
                provider_failures = verify_rows(secondary.get("rows") or [])
                provider_comparison = compare_sources(canonical, secondary)
                if (provider_comparison.get("overlap_bars") or 0) < min(120, minimum_bars):
                    provider_failures.append("cross-source overlap is insufficient")
                if provider_comparison.get("median_close_gap") is None or provider_comparison["median_close_gap"] > 0.01:
                    provider_failures.append("median cross-source close gap exceeds 1%")
                if provider_comparison.get("p95_close_gap") is None or provider_comparison["p95_close_gap"] > 0.03:
                    provider_failures.append("p95 cross-source close gap exceeds 3%")
                return_gaps = [gap for gap in provider_comparison.get("period_return_gaps", {}).values() if gap is not None]
                if not return_gaps or max(return_gaps) > 0.05:
                    provider_failures.append("cross-source horizon-return gap exceeds 5 percentage points")
                if provider_comparison.get("latest_date_gap_days") is None or provider_comparison["latest_date_gap_days"] > 5:
                    provider_failures.append("cross-source latest dates differ by more than five days")
                comparisons[provider] = {**provider_comparison, "status": "fail" if provider_failures else "pass", "failures": provider_failures}
                if provider_failures:
                    asset_degradations.append(f"{provider} comparison excluded: {'; '.join(provider_failures)}")
                else:
                    passing_comparisons.append((provider, provider_comparison))
            if passing_comparisons:
                comparison_provider, comparison = sorted(
                    passing_comparisons,
                    key=lambda item: (-(item[1].get("overlap_bars") or 0), item[1].get("p95_close_gap") or 1),
                )[0]
            else:
                asset_failures.append("no independent secondary series passed cross-source gates")
        if canonical:
            current_price = number(snapshot_prices.get(SNAPSHOT_PRICE_KEYS[symbol]))
            latest_close = number((canonical.get("rows") or [{}])[-1].get("close"))
            anchor_gap = abs(current_price - latest_close) / latest_close if current_price is not None and latest_close not in (None, 0) else None
            if anchor_gap is None or anchor_gap > 0.25:
                asset_failures.append("latest completed close differs from verified current price by more than 25%")
            elif anchor_gap > 0.10:
                asset_degradations.append("latest completed close differs from current price by more than 10%")
        else:
            anchor_gap = None
        checks.append({
            "asset": symbol,
            "status": "fail" if asset_failures else "degraded" if asset_degradations else "pass",
            "canonical_provider": canonical_provider,
            "source_count": len(sources),
            "canonical_bars": len((canonical or {}).get("rows") or []),
            "current_price_anchor_gap": anchor_gap,
            "comparison": comparison,
            "comparison_provider": comparison_provider,
            "comparisons": comparisons,
            "failures": asset_failures,
            "degradations": asset_degradations,
        })
        failures.extend(f"{symbol}: {message}" for message in asset_failures)
        degradations.extend(f"{symbol}: {message}" for message in asset_degradations)
    status = "fail" if failures else "degraded" if degradations or history.get("quality", {}).get("status") == "degraded" else "pass"
    return {
        "schema": 1,
        "verified_at": now_iso(),
        "history_generated_at": history.get("generated_at"),
        "snapshot_generated_at": snapshot.get("generated_at"),
        "status": status,
        "failures": failures,
        "degradations": degradations + list(history.get("quality", {}).get("degradations") or []),
        "checks": checks,
        "policy": {
            "core_assets": sorted(CORE_ASSETS),
            "minimum_sources": 2,
            "median_close_gap_max": 0.01,
            "p95_close_gap_max": 0.03,
            "horizon_return_gap_max": 0.05,
            "research_only": True,
        },
    }


def main() -> int:
    report = verify()
    write_json(OUTPUT_PATH, report)
    print(json.dumps({
        "output": str(OUTPUT_PATH),
        "status": report["status"],
        "failures": len(report["failures"]),
        "degradations": len(report["degradations"]),
    }, ensure_ascii=False))
    return 1 if report["status"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
