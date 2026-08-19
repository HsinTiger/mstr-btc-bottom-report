#!/usr/bin/env python3
"""Independently verify the hourly market-universe artifact."""

from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from verify_daily_data import (
    ETF_BACKUP_COMPONENT_MIN_COVERAGE,
    ETF_MAX_ABS_DAILY_FUND_FLOW_USD,
    ETF_OFFICIAL_COMPONENT_MIN_COVERAGE,
    as_float,
    assert_close,
    classify_verification_status,
    lag_hours,
    now_iso,
    recompute_etf_validation,
    recompute_sector_validation,
    write_json,
)


ROOT = Path(__file__).resolve().parents[1]
MARKET_PATH = ROOT / "data" / "daily" / "market_universe.json"
REPORT_PATH = ROOT / "data" / "daily" / "market_universe_verification.json"
SECTOR_SOURCE_MAX_LAG_HOURS = 0.25


def expected_evidence_metric_ids(market: dict[str, Any]) -> set[str]:
    return {
        "summary.BTC.leverage",
        "summary.ETH.leverage",
        "summary.asset_breadth",
        "summary.sector_lead",
        *(f"assets.{symbol}" for symbol in market.get("assets", {})),
        "derivatives.BTC",
        "derivatives.ETH",
        "etf.BTC",
        "etf.ETH",
        "dat.BTC",
        "dat.ETH",
        *(f"sectors.{name}" for name in market.get("sectors", {})),
        "thesis.btcfi",
        "thesis.gold",
        "thesis.stablecoin",
        "thesis.rwa",
        "thesis.company_share",
        "thesis.company_concentration",
        "thesis.hashrate",
        "thesis.debt",
        "thesis.real_yield",
    }


def evidence_ledger_errors(market: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    sources = market.get("sources", [])
    if not isinstance(sources, list) or not sources:
        return ["source registry is missing"]
    source_ids = [str(item.get("source_id") or "") for item in sources]
    if any(not source_id for source_id in source_ids) or len(source_ids) != len(set(source_ids)):
        errors.append("source registry IDs are missing or duplicated")
    source_index = {str(item.get("source_id")): item for item in sources if item.get("source_id")}
    generated_at = market.get("generated_at")
    fetched_max_lag = as_float(market.get("quality", {}).get("freshness_contract", {}).get("daily_snapshot_max_age_hours")) or 30
    for source_id, item in source_index.items():
        if not str(item.get("url") or "").startswith("https://"):
            errors.append(f"source {source_id} URL is missing or not HTTPS")
        for field in ("provider", "source_tier", "as_of", "as_of_basis", "fetched_at", "detail"):
            if item.get(field) in (None, ""):
                errors.append(f"source {source_id} is missing {field}")
        fetched_lag = lag_hours(item.get("fetched_at"), generated_at)
        if fetched_lag is None or fetched_lag < -0.25 or fetched_lag > fetched_max_lag:
            errors.append(f"source {source_id} fetched_at is future, stale, or invalid")

    ledger = market.get("evidence_ledger", {})
    if ledger.get("schema") != 1 or ledger.get("generated_at") != market.get("generated_at"):
        errors.append("evidence ledger schema or batch binding is invalid")
    metrics = ledger.get("metrics")
    if not isinstance(metrics, dict):
        return errors + ["evidence ledger metrics are missing"]
    expected = expected_evidence_metric_ids(market)
    missing = expected - set(metrics)
    extra = set(metrics) - expected
    if missing:
        errors.append(f"evidence ledger missing metrics: {','.join(sorted(missing))}")
    if extra:
        errors.append(f"evidence ledger has unbound metrics: {','.join(sorted(extra))}")

    for metric_id, item in metrics.items():
        ids = item.get("source_ids")
        validation_ids = item.get("validation_source_ids")
        status = item.get("status")
        if not isinstance(ids, list) or not ids or len(ids) != len(set(ids)):
            errors.append(f"evidence {metric_id} source IDs are missing or duplicated")
            continue
        if any(source_id not in source_index for source_id in ids):
            errors.append(f"evidence {metric_id} references unknown source")
        if item.get("source_count") != len(ids):
            errors.append(f"evidence {metric_id} source count mismatch")
        if not isinstance(validation_ids, list) or any(source_id not in ids for source_id in validation_ids):
            errors.append(f"evidence {metric_id} validation sources are invalid")
        elif item.get("validation_source_count") != len(validation_ids):
            errors.append(f"evidence {metric_id} validation source count mismatch")
        if status not in {"pass", "degraded", "context_only", "fail"}:
            errors.append(f"evidence {metric_id} status is invalid")
        required_fields = ["title", "update_frequency", "verification_method", "freshness_policy", "limitation"]
        if status in {"pass", "context_only"}:
            required_fields.append("as_of")
        for field in required_fields:
            if item.get(field) in (None, ""):
                errors.append(f"evidence {metric_id} is missing {field}")
        if item.get("verification_artifact") != "data/daily/market_universe_verification.json":
            errors.append(f"evidence {metric_id} verifier binding is invalid")
        if metric_id.startswith("assets.") and len(validation_ids or []) < 2:
            errors.append(f"evidence {metric_id} lacks two-source spot quorum")
        if metric_id.startswith("derivatives.") and len(validation_ids or []) < 4:
            errors.append(f"evidence {metric_id} lacks derivatives source coverage")
        if metric_id.startswith("etf."):
            if item.get("formula_verification_artifact") != "data/daily/market_universe_verification.json":
                errors.append(f"evidence {metric_id} formula verifier binding is invalid")
            tiers = {source_index[source_id].get("source_tier") for source_id in validation_ids or [] if source_id in source_index}
            if status == "pass" and (len(validation_ids or []) < 3 or "official_issuer_crosscheck" not in tiers):
                errors.append(f"evidence {metric_id} lacks canonical, official, and backup validation")
        if metric_id.startswith("dat.") and len(validation_ids or []) < 3:
            errors.append(f"evidence {metric_id} lacks representative cross-source coverage")
        if metric_id.startswith("sectors.") and len(validation_ids or []) < 3:
            errors.append(f"evidence {metric_id} lacks sector source quorum")

    for asset in ("BTC", "ETH"):
        entry = metrics.get(f"etf.{asset}", {})
        if entry.get("as_of") != market.get("etf", {}).get(asset, {}).get("as_of"):
            errors.append(f"evidence etf.{asset} date does not match published metric")
    for symbol, item in market.get("assets", {}).items():
        if metrics.get(f"assets.{symbol}", {}).get("as_of") != item.get("as_of"):
            errors.append(f"evidence assets.{symbol} date does not match published metric")
    return list(dict.fromkeys(errors))


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

    snapshot_max_age = as_float(freshness.get("daily_snapshot_max_age_hours")) or 30
    snapshot_lag = lag_hours(market.get("snapshot_generated_at"), generated_at)
    raw_lag = lag_hours(market.get("raw_generated_at"), generated_at)
    if (
        snapshot_lag is None
        or raw_lag is None
        or snapshot_lag < -0.25
        or raw_lag < -0.25
        or snapshot_lag > snapshot_max_age
        or raw_lag > snapshot_max_age
    ):
        failures.append("daily snapshot or raw observations are future, stale, or invalid")

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

    failures.extend(evidence_ledger_errors(market))

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

    # Sector baskets are breadth context, never a core market check. The four
    # aggregators legitimately disagree on 24h return (USD aggregate vs USDT
    # spot), so a missing strict majority is routine. The data layer already
    # fails closed — an unverified basket publishes status "unavailable" with a
    # null value — so a breadth disagreement is recorded as a degradation and no
    # longer freezes BTC/ETH/ETF/macro data for the rest of the day.
    for sector, item in market.get("sectors", {}).items():
        result = recompute_sector_validation(item, generated_at, SECTOR_SOURCE_MAX_LAG_HOURS)
        degradations.extend(f"sector {sector}: {error}" for error in result["errors"])
        if item.get("status") != "cross_source_verified":
            degradations.append(f"sector {sector}: status is not cross_source_verified")
            if item.get("change_24h") is not None or item.get("market_cap_usd") is not None:
                failures.append(f"sector {sector}: unverified basket must not publish a value")

    for asset in ("BTC", "ETH"):
        etf = market.get("etf", {}).get(asset, {})
        if etf.get("status") != "sample_cross_source_verified":
            degradations.append(f"{asset} ETF flow is not cross-source verified")
        else:
            try:
                validation_inputs = json.loads(str(etf.get("validation_inputs_json") or ""))
            except json.JSONDecodeError:
                validation_inputs = {}
            recomputed = recompute_etf_validation(validation_inputs, asset)
            failures.extend(f"{asset} ETF reconstruction: {error}" for error in recomputed["errors"])
            if validation_inputs.get("canonical_provider") not in {"The Block", "Blockworks / Trackinsights", "Bitbo"}:
                failures.append(f"{asset} ETF canonical provider is not approved")
            if validation_inputs.get("canonical_as_of") != etf.get("as_of"):
                failures.append(f"{asset} ETF canonical date does not match published metric")
            assert_close(f"{asset} ETF published 1d flow", recomputed["canonical_component_sum_usd"], etf.get("flow_1d_usd"), failures)
            assert_close(f"{asset} ETF component completeness", recomputed["component_completeness"], etf.get("component_completeness"), failures)
            assert_close(f"{asset} ETF official gap", recomputed["official_gap"], etf.get("official_major_fund_gap"), failures)
            assert_close(f"{asset} ETF official coverage", recomputed["official_coverage"], etf.get("official_major_fund_coverage"), failures)
            assert_close(f"{asset} ETF backup gap", recomputed["backup_max_gap"], etf.get("backup_component_gap"), failures)
            assert_close(f"{asset} ETF backup coverage", recomputed["backup_coverage"], etf.get("backup_component_coverage"), failures)
            assert_close(f"{asset} ETF source count", recomputed["validation_source_count"], etf.get("source_count"), failures)
            for field, days in {"flow_1d_usd": 1, "flow_7d_usd": 7, "flow_30d_usd": 30}.items():
                value = as_float(etf.get(field))
                if value is None or abs(value) > ETF_MAX_ABS_DAILY_FUND_FLOW_USD * days:
                    failures.append(f"{asset} ETF {field} is missing or outside sanity bounds")
            independently_verified = bool(
                recomputed["component_completeness"] is not None
                and recomputed["component_completeness"] >= 0.95
                and recomputed["official_gap"] is not None
                and recomputed["official_gap"] <= 0.05
                and recomputed["official_direction_match"]
                and recomputed["official_coverage"] is not None
                and recomputed["official_coverage"] >= ETF_OFFICIAL_COMPONENT_MIN_COVERAGE
                and recomputed["backup_max_gap"] is not None
                and recomputed["backup_max_gap"] <= 0.05
                and recomputed["backup_direction_match"]
                and recomputed["backup_coverage"] is not None
                and recomputed["backup_coverage"] >= ETF_BACKUP_COMPONENT_MIN_COVERAGE
                and recomputed["validation_source_count"] >= 3
                and recomputed["canonical_total_reconciled"]
                and recomputed["amount_sanity_pass"]
                and not recomputed["errors"]
            )
            if not independently_verified:
                failures.append(f"{asset} ETF verified claim failed independent reconstruction")
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
    print(json.dumps({
        "status": report["status"],
        "failures": report["failures"],
        "degradations": report["degradations"],
    }, ensure_ascii=False))
    return 1 if report["status"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
