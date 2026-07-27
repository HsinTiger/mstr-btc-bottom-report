#!/usr/bin/env python3
"""Independently verify the hourly market-universe artifact."""

from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from verify_daily_data import (
    as_float,
    assert_close,
    classify_verification_status,
    lag_hours,
    now_iso,
    recompute_sector_validation,
    write_json,
)


ROOT = Path(__file__).resolve().parents[1]
MARKET_PATH = ROOT / "data" / "daily" / "market_universe.json"
REPORT_PATH = ROOT / "data" / "daily" / "market_universe_verification.json"
COMPOSITION_DIVERGENT_SECTORS = {"defi", "meme"}


def verify_market_universe(market: dict[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    degradations: list[str] = []
    warnings: list[str] = []
    evidence: list[str] = []
    quality = market.get("quality", {})
    generated_at = market.get("generated_at")
    freshness = quality.get("freshness_contract", {})

    if market.get("schema") != 2:
        failures.append("market universe schema must be 2")
    try:
        generated_time = datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
        if generated_time.tzinfo is None:
            generated_time = generated_time.replace(tzinfo=timezone.utc)
        artifact_age = (datetime.now(timezone.utc) - generated_time).total_seconds() / 3600
    except (TypeError, ValueError):
        artifact_age = None
    artifact_max_age = as_float(freshness.get("artifact_max_age_hours"))
    if artifact_age is None or artifact_max_age is None or artifact_age < -0.25 or artifact_age > artifact_max_age:
        failures.append("market universe artifact timestamp is missing or stale")
    else:
        evidence.append(f"artifact_age_hours={artifact_age:.3f}")

    checks = quality.get("checks")
    summary = quality.get("validation_summary", {})
    if not isinstance(checks, list) or not checks:
        failures.append("market universe quality checks are missing")
        checks = []
    expected_summary = {
        "total": len(checks),
        "passed": sum(item.get("status") == "pass" for item in checks),
        "degraded": sum(item.get("status") == "degraded" for item in checks),
        "failed": sum(item.get("status") == "fail" for item in checks),
        "core_total": sum(bool(item.get("core")) for item in checks),
        "core_passed": sum(bool(item.get("core")) and item.get("status") == "pass" for item in checks),
        "core_failed": sum(bool(item.get("core")) and item.get("status") == "fail" for item in checks),
    }
    for key, value in expected_summary.items():
        if summary.get(key) != value:
            failures.append(f"market universe validation summary mismatch: {key}")
    if any(item.get("core") and item.get("status") != "pass" for item in checks):
        failures.append("at least one core market check is not pass")
    if quality.get("status") == "fail":
        failures.extend(f"collector quality: {item}" for item in quality.get("failures", []))
    elif quality.get("status") == "degraded":
        degradations.extend(f"collector quality: {item}" for item in quality.get("degradations", []))
    elif quality.get("status") != "pass":
        failures.append("market universe quality status is invalid")

    spot_max_lag = as_float(freshness.get("spot_source_max_lag_hours")) or 2
    perpetual_max_lag = as_float(freshness.get("perpetual_source_max_lag_hours")) or 2
    dated_max_lag = as_float(freshness.get("dated_future_source_max_lag_hours")) or 2
    options_max_lag = as_float(freshness.get("options_source_max_lag_hours")) or 2
    volatility_max_lag = as_float(freshness.get("volatility_source_max_lag_hours")) or 3

    required_assets = {"BTC", "ETH", "HYPE", "SOL", "BNB", "XRP", "DOGE"}
    if not required_assets.issubset(market.get("assets", {})):
        failures.append("tracked spot asset universe is incomplete")
    for symbol in sorted(required_assets):
        item = market.get("assets", {}).get(symbol, {})
        observations = item.get("source_observations", {})
        prices = [as_float(value) for value in item.get("source_prices", {}).values()]
        prices = [value for value in prices if value is not None and value > 0]
        if len(prices) < 2 or len(observations) != len(prices) or item.get("source_count") != len(prices):
            failures.append(f"{symbol} spot source quorum or count mismatch")
            continue
        assert_close(f"{symbol} spot median", statistics.median(prices), item.get("price_usd"), failures)
        expected_gap = (max(prices) - min(prices)) / statistics.mean(prices)
        assert_close(f"{symbol} spot source gap", expected_gap, item.get("cross_source_gap"), failures)
        if expected_gap > 0.02:
            failures.append(f"{symbol} spot source gap exceeds 2%")
        for provider, observation in observations.items():
            source_lag = lag_hours(observation.get("as_of"), generated_at)
            if source_lag is None or source_lag < -0.25 or source_lag > spot_max_lag:
                failures.append(f"{symbol} {provider} timestamp outside batch freshness window")

    for symbol in ("BTC", "ETH"):
        derivative = market.get("derivatives", {}).get(symbol, {})
        perpetual = derivative.get("perpetual", {})
        venues = perpetual.get("venues_used", [])
        if not isinstance(venues, list) or len(set(venues)) < 2 or perpetual.get("funding_source_count") != len(venues):
            failures.append(f"{symbol} perpetual venue quorum is incomplete")
        annualized_values = []
        for venue in venues:
            venue_data = perpetual.get(venue, {})
            rate = as_float(venue_data.get("funding_rate"))
            interval = as_float(venue_data.get("funding_interval_hours"))
            if rate is None or interval in (None, 0):
                failures.append(f"{symbol} {venue} funding inputs are incomplete")
                continue
            annualized = rate * 24 / interval * 365
            annualized_values.append(annualized)
            assert_close(f"{symbol} {venue} funding annualization", annualized, venue_data.get("funding_annualized"), failures)
            venue_lag = lag_hours(venue_data.get("as_of"), generated_at)
            if venue_lag is None or venue_lag < -0.25 or venue_lag > perpetual_max_lag:
                failures.append(f"{symbol} {venue} funding timestamp outside batch freshness window")
        if annualized_values:
            assert_close(f"{symbol} median funding", statistics.median(annualized_values), perpetual.get("funding_annualized_median"), failures)

        dated = derivative.get("dated_future", {})
        if dated.get("provider") not in {"Deribit", "OKX"} or as_float(dated.get("annualized_basis")) is None:
            failures.append(f"{symbol} dated-futures contract is incomplete")
        dated_lag = lag_hours(dated.get("as_of"), generated_at)
        if dated_lag is None or dated_lag < -0.25 or dated_lag > dated_max_lag:
            failures.append(f"{symbol} dated-futures timestamp outside batch freshness window")

        options = derivative.get("options", {})
        if as_float(options.get("volatility_value")) is None or as_float(options.get("put_call_open_interest_ratio")) is None:
            failures.append(f"{symbol} options contract is incomplete")
        options_lag = lag_hours(options.get("as_of"), generated_at)
        volatility_lag = lag_hours(options.get("volatility_as_of"), generated_at)
        if options_lag is None or options_lag < -0.25 or options_lag > options_max_lag:
            failures.append(f"{symbol} options timestamp outside batch freshness window")
        if volatility_lag is None or volatility_lag < -0.25 or volatility_lag > volatility_max_lag:
            failures.append(f"{symbol} volatility timestamp outside batch freshness window")

    for sector, item in market.get("sectors", {}).items():
        result = recompute_sector_validation(item, generated_at, spot_max_lag)
        target = degradations if sector.lower() in COMPOSITION_DIVERGENT_SECTORS else failures
        target.extend(f"sector {sector}: {error}" for error in result["errors"])
        if item.get("status") != "cross_source_verified":
            target.append(f"sector {sector}: status is not cross_source_verified")

    for asset in ("BTC", "ETH"):
        if market.get("etf", {}).get(asset, {}).get("status") != "sample_cross_source_verified":
            degradations.append(f"{asset} ETF flow is not cross-source verified")
        if market.get("dat", {}).get(asset, {}).get("status") != "representative_cross_source_verified":
            degradations.append(f"{asset} DAT holdings are not representative cross-source verified")

    failures = list(dict.fromkeys(failures))
    degradations = list(dict.fromkeys(degradations))
    return {
        "schema": 1,
        "agent": "market-universe-verifier",
        "verified_at": now_iso(),
        "market_date": market.get("date"),
        "market_generated_at": generated_at,
        "status": classify_verification_status(failures, degradations),
        "failures": failures,
        "degradations": degradations,
        "warnings": warnings,
        "evidence": evidence,
    }


def main() -> int:
    report = verify_market_universe(json.loads(MARKET_PATH.read_text(encoding="utf-8-sig")))
    write_json(REPORT_PATH, report)
    print(json.dumps({"status": report["status"], "failures": len(report["failures"]), "degradations": len(report["degradations"])}, ensure_ascii=False))
    return 1 if report["status"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
