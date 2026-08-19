#!/usr/bin/env python3
"""Prove market evidence cards fail closed when their audit trail is mutated."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta
from pathlib import Path

import verify_market_universe as verifier


ROOT = Path(__file__).resolve().parents[1]
SOURCE = json.loads((ROOT / "data" / "daily" / "market_universe.json").read_text(encoding="utf-8-sig"))


def expect_error(name: str, source: dict, expected: str) -> None:
    errors = verifier.evidence_ledger_errors(source)
    if not any(expected in error for error in errors):
        raise AssertionError(f"{name}: expected {expected!r}, got {errors}")


def expect_verifier_failure(name: str, source: dict, expected: str) -> None:
    report = verifier.verify_market_universe(source)
    if report["status"] != "fail" or not any(expected in error for error in report["failures"]):
        raise AssertionError(f"{name}: expected failure {expected!r}, got {report}")


def expect_verifier_degradation(name: str, source: dict, expected: str) -> None:
    report = verifier.verify_market_universe(source)
    if any(expected in error for error in report["failures"]):
        raise AssertionError(f"{name}: {expected!r} must degrade, not hard-fail: {report['failures']}")
    if not any(expected in note for note in report["degradations"]):
        raise AssertionError(f"{name}: expected degradation {expected!r}, got {report}")


def main() -> int:
    baseline = verifier.evidence_ledger_errors(copy.deepcopy(SOURCE))
    if baseline:
        raise AssertionError(f"baseline evidence ledger failed: {baseline}")
    verified_etf_asset = next(
        (asset for asset in ("BTC", "ETH") if SOURCE.get("etf", {}).get(asset, {}).get("status") == "sample_cross_source_verified"),
        None,
    )
    if verified_etf_asset is None:
        raise AssertionError("fixture requires at least one independently verified ETF asset")

    missing_card = copy.deepcopy(SOURCE)
    missing_card["evidence_ledger"]["metrics"].pop("etf.BTC")
    expect_error("missing-card", missing_card, "missing metrics")

    missing_official_etf = copy.deepcopy(SOURCE)
    source_index = {item["source_id"]: item for item in missing_official_etf["sources"]}
    etf_entry = missing_official_etf["evidence_ledger"]["metrics"][f"etf.{verified_etf_asset}"]
    etf_entry["validation_source_ids"] = [
        source_id
        for source_id in etf_entry["validation_source_ids"]
        if source_index[source_id]["source_tier"] != "official_issuer_crosscheck"
    ]
    etf_entry["validation_source_count"] = len(etf_entry["validation_source_ids"])
    expect_error("missing-official-etf", missing_official_etf, "lacks canonical, official, and backup validation")

    missing_formula_verifier = copy.deepcopy(SOURCE)
    missing_formula_verifier["evidence_ledger"]["metrics"]["etf.BTC"]["formula_verification_artifact"] = None
    expect_error("missing-formula-verifier", missing_formula_verifier, "formula verifier binding is invalid")

    degraded_optional_etf = copy.deepcopy(SOURCE)
    degraded_optional_etf["etf"]["BTC"]["status"] = "unavailable"
    degraded_optional_etf["etf"]["BTC"]["as_of"] = None
    degraded_entry = degraded_optional_etf["evidence_ledger"]["metrics"]["etf.BTC"]
    degraded_entry["status"] = "degraded"
    degraded_entry["as_of"] = None
    degraded_entry["validation_source_ids"] = []
    degraded_entry["validation_source_count"] = 0
    degraded_errors = verifier.evidence_ledger_errors(degraded_optional_etf)
    if degraded_errors:
        raise AssertionError(f"degraded optional ETF blocked unrelated market cards: {degraded_errors}")

    missing_observation_date = copy.deepcopy(SOURCE)
    missing_observation_date["sources"][0]["as_of"] = None
    expect_error("missing-observation-date", missing_observation_date, "is missing as_of")

    insecure_source = copy.deepcopy(SOURCE)
    insecure_source["sources"][0]["url"] = "http://example.invalid/source"
    expect_error("insecure-source", insecure_source, "URL is missing or not HTTPS")

    unknown_source = copy.deepcopy(SOURCE)
    asset_entry = unknown_source["evidence_ledger"]["metrics"]["assets.BTC"]
    asset_entry["source_ids"].append("unknown-source")
    asset_entry["validation_source_ids"].append("unknown-source")
    asset_entry["source_count"] = len(asset_entry["source_ids"])
    asset_entry["validation_source_count"] = len(asset_entry["validation_source_ids"])
    expect_error("unknown-source", unknown_source, "references unknown source")

    future_fetch = copy.deepcopy(SOURCE)
    future_fetch["sources"][0]["fetched_at"] = "2099-01-01T00:00:00+00:00"
    expect_error("future-fetch", future_fetch, "fetched_at is future, stale, or invalid")

    stale_daily_inputs = copy.deepcopy(SOURCE)
    stale_daily_inputs["snapshot_generated_at"] = "2000-01-01T00:00:00+00:00"
    stale_daily_inputs["raw_generated_at"] = "2000-01-01T00:00:00+00:00"
    expect_verifier_failure("stale-daily-inputs", stale_daily_inputs, "daily snapshot or raw observations")

    tampered_etf_formula = copy.deepcopy(SOURCE)
    tampered_etf_formula["etf"][verified_etf_asset]["flow_1d_usd"] = 9.99e9
    expect_verifier_failure("tampered-etf-formula", tampered_etf_formula, f"{verified_etf_asset} ETF published 1d flow")

    absurd_etf_window = copy.deepcopy(SOURCE)
    absurd_etf_window["etf"][verified_etf_asset]["flow_7d_usd"] = 9.99e99
    expect_verifier_failure("absurd-etf-window", absurd_etf_window, f"{verified_etf_asset} ETF flow_7d_usd is missing or outside sanity bounds")

    stale_sector_source = copy.deepcopy(SOURCE)
    generated_at = datetime.fromisoformat(stale_sector_source["generated_at"].replace("Z", "+00:00"))
    stale_time = (generated_at - timedelta(hours=1)).isoformat()
    stale_sector_source["sectors"]["RWA"]["source_observations"]["CoinGecko"]["as_of"] = stale_time
    # Breadth baskets are context, not a core check: a source falling out of the
    # freshness window degrades the sector, it does not freeze the whole batch.
    expect_verifier_degradation("stale-sector-source", stale_sector_source, "sector RWA")

    # What must still fail closed is publishing a number the sources never agreed on.
    unverified_sector_value = copy.deepcopy(SOURCE)
    unverified_sector_value["sectors"]["RWA"]["status"] = "unavailable"
    unverified_sector_value["sectors"]["RWA"]["change_24h"] = 0.042
    expect_verifier_failure("unverified-sector-value", unverified_sector_value, "must not publish a value")

    print("market evidence mutation tests: PASS (12/12)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
