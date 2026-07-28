#!/usr/bin/env python3
"""Prove the market-context verifier rejects date and source-independence mutations."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import verify_market_context as verifier

ROOT = Path(__file__).resolve().parents[1]
SOURCE = json.loads((ROOT / "data" / "daily" / "market_context.json").read_text(encoding="utf-8-sig"))


def rehash(source: dict) -> None:
    source["payload_hash"] = verifier.canonical_hash(verifier.without_hash(source))


def expect_status(name: str, source: dict, expected: str) -> None:
    rehash(source)
    report = verifier.verify(source)
    if report["status"] != expected:
        raise AssertionError(f"{name}: expected {expected}, got {report['status']} failures={report['failures']}")


def mark_provider_failed(source: dict, provider: str) -> None:
    target = next(item for item in source["source_checks"] if item["provider"] == provider)
    target["status"] = "fail"
    source["quality"]["successful_sources"] = sum(item["status"] == "pass" for item in source["source_checks"])
    source["quality"]["failed_sources"] = sum(item["status"] == "fail" for item in source["source_checks"])
    source["quality"]["status"] = "degraded"


def main() -> int:
    baseline = verifier.verify(copy.deepcopy(SOURCE))
    if baseline["status"] != "pass":
        raise AssertionError(f"baseline failed: {baseline['failures']} {baseline['degradations']}")

    h41_release_as_observation = copy.deepcopy(SOURCE)
    liquidity = h41_release_as_observation["macro"]["liquidity"]
    liquidity["h41_assets_as_of"] = liquidity["h41_release_date"]
    expect_status("h41-release-as-observation", h41_release_as_observation, "fail")

    wrong_prior_date = copy.deepcopy(SOURCE)
    prior = wrong_prior_date["macro"]["liquidity"]
    prior["prior_net_liquidity_as_of"] = prior["prior_component_as_of"]["rrp"]
    expect_status("prior-net-liquidity-date", wrong_prior_date, "fail")

    treasury_self_check = copy.deepcopy(SOURCE)
    rates = treasury_self_check["macro"]["rates"]
    rates["direct_values"]["treasury_10y_pct"] = rates["fred_values"]["treasury_10y_pct"]
    rates["direct_fred_10y_gap_pp"] = 0
    mark_provider_failed(treasury_self_check, "U.S. Treasury")
    expect_status("treasury-fred-self-check", treasury_self_check, "degraded")

    equity_self_check = copy.deepcopy(SOURCE)
    equities = equity_self_check["macro"]["equities"]
    equities["sp500_independent_check"] = copy.deepcopy(equities["sp500"])
    mark_provider_failed(equity_self_check, "Yahoo ^GSPC")
    expect_status("yahoo-fred-self-check", equity_self_check, "degraded")

    print("market context mutation tests: PASS (4/4)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
