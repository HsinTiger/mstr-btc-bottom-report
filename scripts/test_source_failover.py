#!/usr/bin/env python3
"""Deterministic source-failover contract tests with no network calls."""

from __future__ import annotations

from collect_market_universe import quality_checks
from daily_data_pipeline import Observation, now_iso, verified_spot_price
from verify_daily_data import check_spot_source_pool


def observation(name: str, value: float, source: str) -> Observation:
    timestamp = now_iso()
    return Observation(name, value, source, "https://fixture.invalid", timestamp, True, as_of=timestamp, basis="spot")


def market_fixture(source_count: int = 2, gap: float = 0.01) -> dict:
    timestamp = now_iso()
    observations = {
        "CoinGecko": {"price_usd": 100.0, "as_of": timestamp, "quote_asset": "USD aggregate"},
        "Kraken": {"price_usd": 100.0 * (1 + gap), "as_of": timestamp, "quote_asset": "USD"},
    }
    if source_count == 1:
        observations = {"Kraken": observations["Kraken"]}
    prices = [item["price_usd"] for item in observations.values()]
    asset_gap = (max(prices) - min(prices)) / (sum(prices) / len(prices)) if len(prices) >= 2 else None

    def derivatives() -> dict:
        return {
            "perpetual": {
                "funding_source_count": 2,
                "funding_annualized_median": 0.05,
                "venues_used": ["okx", "hyperliquid"],
                "venue_errors": ["Binance HTTP 451，已切換備援來源"],
                "okx": {"as_of": timestamp},
                "hyperliquid": {"as_of": timestamp},
            },
            "dated_future": {
                "provider": "OKX",
                "annualized_basis": 0.04,
                "as_of": timestamp,
                "fallback_errors": ["Deribit 暫時無法取得，已切換備援來源"],
            },
            "options": {
                "provider": "OKX",
                "volatility_value": 55.0,
                "put_call_open_interest_ratio": 0.8,
                "as_of": timestamp,
                "volatility_as_of": timestamp,
                "contracts_observed": 10,
                "open_interest_observed_contracts": 10,
                "volume_observed_contracts": 10,
                "fallback_errors": ["Deribit 暫時無法取得，已切換備援來源"],
            },
        }

    return {
        "assets": {
            "BTC": {
                "price_usd": sum(prices) / len(prices) if source_count >= 2 else None,
                "source_count": source_count,
                "cross_source_gap": asset_gap,
                "source_observations": observations,
            }
        },
        "derivatives": {"BTC": derivatives(), "ETH": derivatives()},
        "sectors": {},
        "dat": {},
        "etf": {"BTC": {"status": "unavailable", "as_of": timestamp}, "ETH": {"status": "unavailable"}},
        "btc_thesis": {"quality": {"status": "pass"}},
    }


def test_daily_source_pool() -> None:
    observations = [
        observation("btc_usd_coingecko", 100.0, "CoinGecko"),
        observation("btc_usd_kraken", 101.0, "Kraken"),
    ]
    price, basis = verified_spot_price(
        observations,
        ["btc_usd_coingecko", "btc_usd_coinbase", "btc_usd_kraken"],
        "BTC",
    )
    assert price == 100.5
    failures: list[str] = []
    warnings: list[str] = []
    check_spot_source_pool(
        "BTC spot",
        ["btc_usd_coingecko", "btc_usd_coinbase", "btc_usd_kraken"],
        {item.name: item.to_dict() for item in observations},
        price,
        basis,
        failures,
        warnings,
        [],
    )
    assert not failures, failures


def test_daily_single_source_fails() -> None:
    item = observation("btc_usd_kraken", 100.0, "Kraken")
    failures: list[str] = []
    check_spot_source_pool(
        "BTC spot",
        ["btc_usd_coingecko", "btc_usd_coinbase", "btc_usd_kraken"],
        {item.name: item.to_dict()},
        None,
        {"source_count": 1, "selected_observations": [item.name]},
        failures,
        [],
        [],
    )
    assert failures and "至少需要 2 個" in failures[0]


def test_daily_divergence_fails() -> None:
    observations = [
        observation("btc_usd_coingecko", 100.0, "CoinGecko"),
        observation("btc_usd_kraken", 103.0, "Kraken"),
    ]
    price, basis = verified_spot_price(observations, [item.name for item in observations], "BTC")
    failures: list[str] = []
    check_spot_source_pool(
        "BTC spot",
        [item.name for item in observations],
        {item.name: item.to_dict() for item in observations},
        price,
        basis,
        failures,
        [],
        [],
    )
    assert any("來源池價差" in item for item in failures), failures


def test_market_incident_does_not_downgrade_verified_field() -> None:
    quality = quality_checks(market_fixture(), ["Binance 現貨 HTTP 451，已切換備援來源"])
    assert not quality["failures"], quality["failures"]
    assert quality["validation_summary"]["core_failed"] == 0
    assert quality["source_incidents"]
    assert not any("451" in item for item in quality["degradations"])


def test_market_single_source_and_divergence_fail() -> None:
    single_source = quality_checks(market_fixture(source_count=1), [])
    assert single_source["status"] == "fail"
    divergent = quality_checks(market_fixture(gap=0.03), [])
    assert divergent["status"] == "fail"


def main() -> int:
    test_daily_source_pool()
    test_daily_single_source_fails()
    test_daily_divergence_fails()
    test_market_incident_does_not_downgrade_verified_field()
    test_market_single_source_and_divergence_fail()
    print("source failover contract tests: PASS (5/5)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
