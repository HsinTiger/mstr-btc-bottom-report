#!/usr/bin/env python3
"""Deterministic source-failover contract tests with no network calls."""

from __future__ import annotations

import daily_data_pipeline as daily_pipeline

from collect_market_universe import (
    BitboTreasuryTableParser,
    FRESHNESS_CONTRACT,
    TreasuryTableParser,
    apply_official_dat_overlays,
    compute_sector_baskets,
    dat_cross_source_validation,
    enforce_official_overlay_contract,
    lag_hours_at,
    quality_checks,
    select_dat_base_provider,
    select_dat_validated_base,
)
from daily_data_pipeline import (
    Observation,
    etf_backup_sample_validation,
    etf_quorum_passes,
    now_iso,
    parse_strategy_atm_periods,
    parse_strategy_capital_update,
    relative_difference,
    verified_spot_price,
)
from verify_daily_data import classify_verification_status, check_spot_source_pool, recompute_dat_official_overlay, recompute_dat_validation, recompute_etf_validation, recompute_sector_validation


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
        "generated_at": timestamp,
        "snapshot_generated_at": timestamp,
        "raw_generated_at": timestamp,
        "source_batch_id": timestamp,
        "raw_batch_id": timestamp,
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


def test_market_lineage_mismatch_is_core_failure() -> None:
    fixture = market_fixture()
    fixture["raw_batch_id"] = "different-batch"
    quality = quality_checks(fixture, [])
    lineage = next(item for item in quality["checks"] if item["check_id"] == "daily_snapshot_lineage")
    assert quality["status"] == "fail"
    assert lineage["status"] == "fail"
    assert lineage["core"] is True


def test_freshness_uses_batch_time_not_view_time() -> None:
    generated_at = "2026-07-22T03:46:58+00:00"
    completed_dvol = "2026-07-22T02:00:00+00:00"
    lag = lag_hours_at(generated_at, completed_dvol)
    assert lag is not None and lag < FRESHNESS_CONTRACT["volatility_source_max_lag_hours"]
    quality = quality_checks(market_fixture(), [])
    assert quality["freshness_contract"] == FRESHNESS_CONTRACT
    assert FRESHNESS_CONTRACT["thesis_credit_max_lag_hours"] == 36


def test_warning_only_does_not_degrade_verified_batch() -> None:
    assert classify_verification_status([], []) == "pass"
    assert classify_verification_status([], ["material source degradation"]) == "degraded"
    assert classify_verification_status(["hard contract failure"], []) == "fail"


def sector_provider_fixture(providers: int = 3, divergence: float = 0.0) -> dict:
    timestamp = now_iso()
    symbols = ["ONDO", "LINK", "XLM", "PAXG", "XAUT", "BTC", "ETH", "BNB", "SOL", "XRP", "UNI", "AAVE", "LDO", "ENA", "PENDLE", "DOGE", "SHIB", "PEPE", "BONK", "WIF"]
    result = {}
    for index, provider in enumerate(("CoinGecko", "CoinPaprika", "CoinLore", "Binance")[:providers]):
        result[provider] = {
            symbol: {
                "change_24h": 0.01 + (divergence if index == 2 else index * 0.0005),
                "market_cap_usd": None if provider == "Binance" else 1_000_000.0,
                "volume_24h_usd": None if provider == "Binance" else 100_000.0,
                "as_of": timestamp,
            }
            for symbol in symbols
        }
    return result


def test_sector_basket_quorum_survives_one_provider_failure() -> None:
    complete = sector_provider_fixture(providers=4)
    for failed_provider in complete:
        sectors = compute_sector_baskets({provider: assets for provider, assets in complete.items() if provider != failed_provider}, [f"{failed_provider} unavailable"])
        assert all(item["status"] == "cross_source_verified" for item in sectors.values())
        assert all(item["source_count"] == 3 for item in sectors.values())


def test_sector_basket_single_source_and_divergence_do_not_publish() -> None:
    single = compute_sector_baskets(sector_provider_fixture(providers=1))
    assert all(item["change_24h"] is None for item in single.values())
    divergent = compute_sector_baskets(sector_provider_fixture(providers=3, divergence=0.02))
    assert all(item["status"] == "unavailable" for item in divergent.values())


def test_verifier_recomputes_sector_claim() -> None:
    item = compute_sector_baskets(sector_provider_fixture(providers=3))["RWA"]
    assert not recompute_sector_validation(item)["errors"]
    item["change_24h"] = 0.5
    assert recompute_sector_validation(item)["errors"]


def test_strategy_capital_update_and_no_sale_period_parsers() -> None:
    update = parse_strategy_capital_update(
        "Strategy completed the repurchase of $1.5 billion aggregate principal amount of its 0% Convertible Senior Notes due 2029, "
        "has $6.7 billion aggregate principal amount of convertible notes and $15.5 billion aggregate notional amount of preferred stock outstanding. "
        "The regular dividend rate per annum on STRC will increase to 12.00%."
    )
    assert update == {
        "strc_rate": 0.12,
        "zero_coupon_debt_repurchase_musd": 1500.0,
        "official_convertible_debt_total_musd": 6700.0,
        "official_preferred_total_musd": 15500.0,
    }
    periods = parse_strategy_atm_periods("During the period between May 18, 2026 and May 25, 2026, Strategy did not sell any shares under its at-the-market offering program.")
    assert periods == [{"start": "2026-05-18", "end": "2026-05-25", "shares_sold": {}}]


def test_treasury_table_parser_ignores_css_angle_brackets() -> None:
    parser = TreasuryTableParser()
    parser.feed(
        '<section id="holders"><table data-slot="table"><tbody><tr>'
        '<td>1</td><td>US</td><td><a class="[&>svg]:size-3" href="/public-companies/strategy">Strategy</a>'
        '<span data-slot="badge">MSTR</span></td><td>₿ 843,775</td></tr></tbody></table></section>'
    )
    assert parser.rows == [{
        "name": "Strategy",
        "symbol": "MSTR",
        "holdings": 843775.0,
        "detail_path": "/public-companies/strategy",
    }]


def test_bitbo_treasury_parser_extracts_symbol_and_holdings() -> None:
    parser = BitboTreasuryTableParser()
    parser.feed(
        '<table><tbody><tr><td class="td-company">Strategy</td>'
        '<td><img alt="US"></td><td class="td-symbol">MSTR:NADQ</td>'
        '<td class="td-company_btc">843,775</td></tr></tbody></table>'
    )
    assert parser.rows == [{"name": "Strategy", "symbol": "MSTR", "holdings": 843775.0}]


def test_dat_quorum_survives_one_provider_failure() -> None:
    coingecko = {
        "MSTR": {"holdings": 843775.0},
        "XXI": {"holdings": 43514.0},
        "MARA": {"holdings": 35303.0},
    }
    bitcoin_treasuries = {
        "MSTR": {"holdings": 843775.0},
        "XXI": {"holdings": 43514.0},
        "MARA": {"holdings": 36303.0},
    }
    bitbo = {
        "MSTR": {"holdings": 843775.0},
        "XXI": {"holdings": 43514.0},
        "MARA": {"holdings": 35303.0},
    }
    sec = {"MSTR": {"holdings": 843775.0}}
    all_sources = dat_cross_source_validation(
        "BTC",
        {"CoinGecko": coingecko, "BitcoinTreasuries.net": bitcoin_treasuries, "Bitbo Bitcoin Treasuries": bitbo, "SEC official filings": sec},
        "CoinGecko",
        1_285_000.0,
        provider_totals={"CoinGecko": 1_285_000.0, "BitcoinTreasuries.net": 923_592.0, "Bitbo Bitcoin Treasuries": 922_592.0, "SEC official filings": 843_775.0},
    )
    assert all_sources["status"] == "representative_cross_source_verified"
    assert all_sources["non_base_provider_failure_tolerant"] is True
    assert all_sources["base_provider_failure_tolerant"] is True
    assert all(status == "representative_cross_source_verified" for status in all_sources["base_provider_failure_results"].values())
    after_failure = dat_cross_source_validation(
        "BTC",
        {"CoinGecko": coingecko, "Bitbo Bitcoin Treasuries": bitbo, "SEC official filings": sec},
        "CoinGecko",
        1_285_000.0,
        provider_totals={"CoinGecko": 1_285_000.0, "Bitbo Bitcoin Treasuries": 922_592.0, "SEC official filings": 843_775.0},
    )
    assert after_failure["status"] == "representative_cross_source_verified"
    assert after_failure["representative_coverage_ratio"] > 0.60


def test_dat_divergence_fails_quorum() -> None:
    result = dat_cross_source_validation(
        "ETH",
        {
            "CoinGecko": {"BMNR": {"holdings": 5_777_468.0}, "SBET": {"holdings": 868_699.0}},
            "SEC official filings": {"BMNR": {"holdings": 5_000_000.0}, "SBET": {"holdings": 886_725.0}},
        },
        "CoinGecko",
        7_788_049.0,
    )
    assert result["status"] == "quorum_failed"


def test_dat_excludes_disclosed_outlier_without_hiding_it() -> None:
    result = dat_cross_source_validation(
        "BTC",
        {
            "CoinGecko": {"MSTR": {"holdings": 843_775.0}, "MARA": {"holdings": 35_303.0}, "XXI": {"holdings": 43_514.0}},
            "BitcoinTreasuries.net": {"MSTR": {"holdings": 843_775.0}, "MARA": {"holdings": 36_303.0}, "XXI": {"holdings": 43_514.0}},
            "Bitbo Bitcoin Treasuries": {"MSTR": {"holdings": 843_775.0}, "MARA": {"holdings": 35_303.0}, "XXI": {"holdings": 37_229.7}},
            "SEC official filings": {"MSTR": {"holdings": 843_775.0}},
        },
        "CoinGecko",
        922_592.0,
        assess_resilience=False,
    )
    assert result["status"] == "representative_cross_source_verified"
    assert result["excluded_outlier_count"] == 1
    xxi = next(item for item in result["comparisons"] if item["symbol"] == "XXI")
    assert xxi["excluded_outlier_provider_values"] == {"Bitbo Bitcoin Treasuries": 37_229.7}


def test_dat_comparison_requires_base_provider() -> None:
    result = dat_cross_source_validation(
        "BTC",
        {
            "CoinGecko": {"OTHER": {"holdings": 1_000_000.0}},
            "BitcoinTreasuries.net": {"MSTR": {"holdings": 843_775.0}},
            "SEC official filings": {"MSTR": {"holdings": 843_775.0}},
        },
        "CoinGecko",
        1_000_000.0,
    )
    assert result["status"] == "quorum_failed"
    assert result["matched_company_count"] == 0


def test_dat_unmapped_official_company_does_not_change_total() -> None:
    canonical, adjustment, incidents = apply_official_dat_overlays(
        "CoinGecko",
        {"MSTR": {"symbol": "MSTR", "holdings": 843_000.0}},
        {"MSTR": {"symbol": "MSTR", "holdings": 843_775.0}, "SBET": {"symbol": "SBET", "holdings": 900_000.0}},
    )
    assert adjustment == 775.0
    assert set(canonical) == {"MSTR"}
    assert any("SBET" in incident and "未加入總量" in incident for incident in incidents)
    validation = {"status": "representative_cross_source_verified"}
    enforce_official_overlay_contract(validation, {"MSTR": canonical["MSTR"]}, {"MSTR": canonical["MSTR"], "SBET": {"holdings": 900_000.0}}, {"MSTR", "SBET"})
    assert validation["status"] == "quorum_failed"
    assert validation["official_overlay_complete"] is False


def test_dat_partial_canonical_falls_back() -> None:
    selected = select_dat_base_provider({
        "CoinGecko": {"total_holdings": None, "companies": {"MSTR": {"holdings": 1.0}}},
        "BitcoinTreasuries.net": {"total_holdings": 10.0, "companies": {"MSTR": {"holdings": 10.0}}},
    })
    assert selected == "BitcoinTreasuries.net"


def test_dat_divergent_canonical_switches_to_passing_base() -> None:
    official = {"MSTR": {"holdings": 843_775.0}}
    payloads = {
        "CoinGecko": {
            "total_holdings": 1_000_000.0,
            "companies": {"MSTR": {"holdings": 700_000.0}, "MARA": {"holdings": 35_303.0}, "XXI": {"holdings": 43_514.0}},
        },
        "BitcoinTreasuries.net": {
            "total_holdings": 923_592.0,
            "companies": {"MSTR": {"holdings": 843_775.0}, "MARA": {"holdings": 36_303.0}, "XXI": {"holdings": 43_514.0}},
        },
        "Bitbo Bitcoin Treasuries": {
            "total_holdings": 9_000_000.0,
            "companies": {"MSTR": {"holdings": 843_775.0}, "MARA": {"holdings": 35_303.0}, "XXI": {"holdings": 37_229.7}},
        },
        "SEC official filings": {"total_holdings": 843_775.0, "companies": official},
    }
    provider, validation = select_dat_validated_base("BTC", payloads, official)
    assert provider == "BitcoinTreasuries.net"
    assert validation["status"] == "representative_cross_source_verified"
    assert validation["base_candidate_results"]["CoinGecko"]["status"] == "quorum_failed"


def etf_quorum(**overrides: object) -> bool:
    arguments: dict[str, object] = {
        "canonical_provider": "The Block",
        "component_completeness": 1.0,
        "official_gap": 0.01,
        "official_component_coverage": 0.60,
        "backup_component_gap": 0.01,
        "backup_component_coverage": 0.60,
        "backup_same_date": True,
        "canonical_total_reconciled": True,
        "amount_sanity_pass": True,
        "validation_source_count": 3,
        "updated_age_hours": 2.0,
        "market_age_days": 2,
    }
    arguments.update(overrides)
    return etf_quorum_passes(**arguments)


def test_etf_quorum_accepts_verified_fallback_canonical() -> None:
    assert etf_quorum(canonical_provider="The Block")
    assert etf_quorum(canonical_provider="Blockworks / Trackinsights")
    assert etf_quorum(canonical_provider="Bitbo")


def test_etf_quorum_rejects_missing_backup_or_official_divergence() -> None:
    assert not etf_quorum(validation_source_count=2)
    assert not etf_quorum(official_gap=0.06)
    assert not etf_quorum(official_component_coverage=0.20)
    assert not etf_quorum(backup_component_gap=None)
    assert not etf_quorum(canonical_provider="summary-only provider")


def test_etf_quorum_rejects_incomplete_fund_roster() -> None:
    assert not etf_quorum(component_completeness=0.94)


def test_etf_quorum_rejects_implausible_amounts() -> None:
    assert not etf_quorum(amount_sanity_pass=False)
    assert not etf_quorum(canonical_total_reconciled=False)


def test_etf_backup_requires_same_market_date() -> None:
    result = etf_backup_sample_validation(
        "BTC",
        "The Block",
        {"date": "2026-07-20", "components_usd": {"IBIT": 100_000_000.0}},
        {
            "The Block": {},
            "Bitbo": {"series": [{"date": "2026-07-19", "components_usd": {"IBIT": 100_000_000.0}}]},
        },
    )
    assert result["provider"] is None
    assert result["maximum_component_gap"] is None


def test_etf_backup_rejects_extreme_same_fund_amount() -> None:
    result = etf_backup_sample_validation(
        "BTC",
        "The Block",
        {"date": "2026-07-20", "components_usd": {"IBIT": 1_000_000_000.0, "FBTC": 10_000_000.0}},
        {
            "The Block": {},
            "Bitbo": {"series": [{"date": "2026-07-20", "components_usd": {"IBIT": 1_000_000_000.0, "FBTC": 20_000_000.0}}]},
        },
    )
    assert result["normalized_gap"] < 0.05
    assert result["maximum_component_gap"] == 0.10
    assert not etf_quorum(backup_component_gap=result["maximum_component_gap"])


def test_etf_backup_accepts_same_date_aggregate_total() -> None:
    result = etf_backup_sample_validation(
        "ETH",
        "The Block",
        {"date": "2026-07-21", "flow_usd": 37_100_000.0, "components_usd": {"ETHA": 34_300_000.0}},
        {
            "The Block": {},
            "CoinMarketCap ETF": {"series": [{"date": "2026-07-21", "flow_usd": 37_500_000.0, "components_usd": {}}]},
        },
    )
    assert result["validation_type"] == "same_date_aggregate_total"
    assert result["gross_component_coverage"] == 1.0
    assert result["maximum_component_gap"] == 0.004


def test_etf_selects_latest_fully_verified_market_date() -> None:
    roster = sorted(daily_pipeline.ETF_EXPECTED_US_SPOT_TICKERS["ETH"])
    def row(as_of: str, etha_flow: float) -> dict:
        components = {ticker: 0.0 for ticker in roster}
        components["ETHA"] = etha_flow
        return {
            "date": as_of,
            "flow_usd": etha_flow,
            "components_usd": components,
            "component_count": len(roster),
            "component_completeness": 1.0,
        }
    providers = {
        "The Block": {
            "provider": "The Block",
            "url": "https://fixture.invalid/theblock",
            "updated_at": now_iso(),
            "expected_tickers": roster,
            "series": [row("2026-07-17", 0.0), row("2026-07-20", 50_000_000.0), row("2026-07-21", 70_000_000.0)],
        },
        "CoinMarketCap ETF": {
            "provider": "CoinMarketCap ETF",
            "url": "https://fixture.invalid/cmc",
            "series": [
                {"date": "2026-07-20", "flow_usd": 50_000_000.0, "components_usd": {}},
                {"date": "2026-07-21", "flow_usd": 70_000_000.0, "components_usd": {}},
            ],
        },
    }
    original_current = daily_pipeline.ishares_holding
    original_prior = daily_pipeline.prior_ishares_holding
    def fake_current(asset: str, as_of: str) -> dict:
        if as_of == "2026-07-21":
            raise ValueError("official T+1 holding not published")
        return {"units": 2_000_000.0, "market_value_usd": 2_000_000_000.0, "as_of": as_of, "url": "https://fixture.invalid/ishares"}
    def fake_prior(asset: str, as_of: str) -> dict:
        return {"units": 1_950_000.0, "market_value_usd": 1_950_000_000.0, "as_of": "2026-07-17", "url": "https://fixture.invalid/ishares"}
    daily_pipeline.ishares_holding = fake_current
    daily_pipeline.prior_ishares_holding = fake_prior
    try:
        observations = daily_pipeline.build_etf_flow_observations("ETH", providers, [])
    finally:
        daily_pipeline.ishares_holding = original_current
        daily_pipeline.prior_ishares_holding = original_prior
    status = next(item for item in observations if item.name == "eth_etf_flow_status")
    assert status.value == "sample_cross_source_verified"
    assert status.as_of == "2026-07-20"


def test_verifier_recomputes_etf_evidence() -> None:
    inputs = {
        "canonical_provider": "The Block",
        "canonical_as_of": "2026-07-20",
        "canonical_components_usd": {"IBIT": 120_000_000.0, "FBTC": 30_000_000.0},
        "canonical_total_usd": 150_000_000.0,
        "gross_component_flow_usd": 150_000_000.0,
        "component_count": 2,
        "expected_ticker_count": 2,
        "expected_tickers": ["IBIT", "FBTC"],
        "official_ticker": "IBIT",
        "official_component_usd": 120_000_000.0,
        "official_proxy_usd": 119_000_000.0,
        "backup_sample": {
            "provider": "Bitbo",
            "as_of": "2026-07-20",
            "matched_tickers": ["IBIT", "FBTC"],
            "canonical_values_usd": {"IBIT": 120_000_000.0, "FBTC": 30_000_000.0},
            "backup_values_usd": {"IBIT": 121_000_000.0, "FBTC": 29_000_000.0},
        },
    }
    result = recompute_etf_validation(inputs)
    assert not result["errors"]
    assert result["validation_source_count"] == 3
    assert result["backup_max_gap"] == 0.01


def test_verifier_recomputes_dat_claim() -> None:
    item = {
        "total_holdings_base_provider": "CoinGecko",
        "total_holdings_base": 1_000_000.0,
        "official_overlay_adjustment": 775.0,
        "validation": {
            "official_overlay_complete": True,
            "comparisons": [
                {
                    "symbol": "MSTR",
                    "provider_values": {"CoinGecko": 843_000.0, "SEC official filings": 843_775.0},
                    "consensus_provider_values": {"CoinGecko": 843_000.0, "SEC official filings": 843_775.0},
                    "excluded_outlier_provider_values": {},
                    "median_holdings": 843_387.5,
                    "max_relative_gap": 775.0 / 843_387.5,
                },
                {
                    "symbol": "MARA",
                    "provider_values": {"CoinGecko": 100_000.0, "Bitbo Bitcoin Treasuries": 100_000.0},
                    "consensus_provider_values": {"CoinGecko": 100_000.0, "Bitbo Bitcoin Treasuries": 100_000.0},
                    "excluded_outlier_provider_values": {},
                    "median_holdings": 100_000.0,
                    "max_relative_gap": 0.0,
                },
            ],
        },
    }
    result = recompute_dat_validation(item)
    assert not result["errors"]
    assert result["status"] == "representative_cross_source_verified"
    overlay = recompute_dat_official_overlay(item, {"MSTR": 843_775.0})
    assert not overlay["errors"]
    assert overlay["official_overlay_adjustment"] == 775.0
    assert overlay["expected_total_holdings"] == 1_000_775.0
    tampered = {**item, "official_overlay_adjustment": 123_456_789.0, "total_holdings": 124_456_789.0}
    tampered_overlay = recompute_dat_official_overlay(tampered, {"MSTR": 843_775.0})
    assert tampered_overlay["official_overlay_adjustment"] != tampered["official_overlay_adjustment"]
    assert tampered_overlay["expected_total_holdings"] != tampered["total_holdings"]


def test_etf_official_gap_uses_absolute_floor_near_zero() -> None:
    assert relative_difference(0.0, -1_000_000.0, scale_floor=100_000_000.0) == 0.01
    assert relative_difference(0.0, -6_000_000.0, scale_floor=100_000_000.0) == 0.06


def main() -> int:
    tests = [
        test_daily_source_pool,
        test_daily_single_source_fails,
        test_daily_divergence_fails,
        test_market_incident_does_not_downgrade_verified_field,
        test_market_single_source_and_divergence_fail,
        test_market_lineage_mismatch_is_core_failure,
        test_freshness_uses_batch_time_not_view_time,
        test_warning_only_does_not_degrade_verified_batch,
        test_sector_basket_quorum_survives_one_provider_failure,
        test_sector_basket_single_source_and_divergence_do_not_publish,
        test_verifier_recomputes_sector_claim,
        test_strategy_capital_update_and_no_sale_period_parsers,
        test_treasury_table_parser_ignores_css_angle_brackets,
        test_bitbo_treasury_parser_extracts_symbol_and_holdings,
        test_dat_quorum_survives_one_provider_failure,
        test_dat_divergence_fails_quorum,
        test_dat_excludes_disclosed_outlier_without_hiding_it,
        test_dat_comparison_requires_base_provider,
        test_dat_unmapped_official_company_does_not_change_total,
        test_dat_partial_canonical_falls_back,
        test_dat_divergent_canonical_switches_to_passing_base,
        test_etf_quorum_accepts_verified_fallback_canonical,
        test_etf_quorum_rejects_missing_backup_or_official_divergence,
        test_etf_quorum_rejects_incomplete_fund_roster,
        test_etf_quorum_rejects_implausible_amounts,
        test_etf_backup_requires_same_market_date,
        test_etf_backup_rejects_extreme_same_fund_amount,
        test_etf_backup_accepts_same_date_aggregate_total,
        test_etf_selects_latest_fully_verified_market_date,
        test_verifier_recomputes_etf_evidence,
        test_verifier_recomputes_dat_claim,
        test_etf_official_gap_uses_absolute_floor_near_zero,
    ]
    for test in tests:
        test()
    print(f"source failover and freshness contract tests: PASS ({len(tests)}/{len(tests)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
