#!/usr/bin/env python3
"""Independent data-verification agent for the daily MSTR/BTC dataset."""

from __future__ import annotations

import json
import math
import statistics
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "daily"
RAW_PATH = DATA_DIR / "raw_observations.json"
SNAPSHOT_PATH = DATA_DIR / "latest_snapshot.json"
REPORT_PATH = DATA_DIR / "agent_verification_report.json"
MARKET_UNIVERSE_PATH = DATA_DIR / "market_universe.json"
TROY_OZ_PER_METRIC_TONNE = 32_150.746568627


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def as_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        value = float(value)
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    except (TypeError, ValueError):
        return None


def obs_map(raw: dict[str, Any]) -> dict[str, Any]:
    return {item["name"]: item for item in raw.get("observations", [])}


def unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def pct_gap(a: float, b: float) -> float:
    return abs(a - b) / ((abs(a) + abs(b)) / 2)


def age_days(value: Any) -> int | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text).date()
    except ValueError:
        try:
            parsed = date.fromisoformat(text[:10])
        except ValueError:
            return None
    return (datetime.now(timezone.utc).date() - parsed).days


def age_hours(value: Any) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - parsed).total_seconds() / 3600
    except ValueError:
        return None


def check_observation_freshness(
    name: str,
    item: dict[str, Any] | None,
    warn_after_days: int,
    fail_after_days: int,
    failures: list[str],
    degradations: list[str],
    evidence: list[str],
) -> None:
    if not item or not item.get("ok"):
        degradations.append(f"{name}: observation unavailable")
        return
    age = age_days(item.get("as_of"))
    if age is None:
        degradations.append(f"{name}: missing structured as_of date")
        return
    evidence.append(f"{name} as_of={item.get('as_of')} age_days={age}")
    if age > fail_after_days:
        failures.append(f"{name}: stale {age} days > {fail_after_days}")
    elif age > warn_after_days:
        degradations.append(f"{name}: stale {age} days > {warn_after_days}")


def check_cross_source(name: str, left: float | None, right: float | None, threshold: float, failures: list[str], warnings: list[str]) -> None:
    if left is None or right is None:
        failures.append(f"{name}: 缺少交叉來源")
        return
    gap = pct_gap(left, right)
    if gap > threshold:
        failures.append(f"{name}: 來源差距 {gap:.2%} > {threshold:.2%}")
    elif gap > threshold / 2:
        warnings.append(f"{name}: 來源差距 {gap:.2%} 接近門檻")


def detail_field(item: dict[str, Any] | None, key: str) -> str | None:
    detail = str((item or {}).get("detail") or "")
    prefix = f"{key}="
    for part in detail.split():
        if part.startswith(prefix):
            return part[len(prefix):]
    return None


def quote_bases_comparable(left_basis: str | None, right_basis: str | None) -> bool:
    if not left_basis or not right_basis:
        return False
    same_basis_groups = [
        {"regular_market_close", "latest_daily_close"},
        {"regular_or_delayed_quote"},
    ]
    return any(left_basis in group and right_basis in group for group in same_basis_groups)


def check_equity_cross_source(
    ticker: str,
    yahoo_item: dict[str, Any] | None,
    nasdaq_item: dict[str, Any] | None,
    failures: list[str],
    degradations: list[str],
    warnings: list[str],
    evidence: list[str],
) -> None:
    yahoo = as_float((yahoo_item or {}).get("value"))
    nasdaq = as_float((nasdaq_item or {}).get("value"))
    label = f"{ticker.upper()} equity"
    if yahoo is None or nasdaq is None:
        degradations.append(f"{ticker.upper()} 缺少股票第二來源，前端需標 degraded")
        return

    yahoo_basis = detail_field(yahoo_item, "quote_basis")
    nasdaq_basis = detail_field(nasdaq_item, "quote_basis")
    gap = pct_gap(yahoo, nasdaq)
    evidence.append(
        f"{ticker.upper()} Yahoo/Nasdaq={yahoo}/{nasdaq}; "
        f"basis={yahoo_basis}/{nasdaq_basis}; gap={gap:.2%}"
    )

    if quote_bases_comparable(yahoo_basis, nasdaq_basis):
        if gap > 0.02:
            failures.append(f"{label}: 同報價基準來源差距 {gap:.2%} > 2.00%")
        elif gap > 0.01:
            warnings.append(f"{label}: 同報價基準來源差距 {gap:.2%} 接近門檻")
        return

    if gap > 0.02:
        degradations.append(
            f"{label}: Yahoo={yahoo_basis or 'unknown'}、Nasdaq={nasdaq_basis or 'unknown'} 報價基準不同，"
            f"差距 {gap:.2%} 不列為硬失敗；每日快照以 Yahoo regular-market close 為主，Nasdaq 作延伸時段/備援檢查"
        )
    elif gap > 0.01:
        warnings.append(
            f"{label}: 報價基準不同且差距 {gap:.2%}，需留意盤前/盤後波動"
        )


def recompute_metrics(snapshot: dict[str, Any]) -> dict[str, float | bool | None]:
    prices = snapshot.get("metrics", {}).get("prices", {})
    inputs = snapshot.get("metrics", {}).get("manual_inputs", {})
    btc_px = as_float(prices.get("btc_usd"))
    mstr_px = as_float(prices.get("mstr_usd"))
    strc_px = as_float(prices.get("strc_usd"))
    preferred = inputs.get("preferred", {})
    pref_total = sum(as_float(item.get("notional_musd")) or 0 for item in preferred.values())
    annual_div = sum((as_float(item.get("notional_musd")) or 0) * (as_float(item.get("rate")) or 0) for item in preferred.values())
    annual_obligation = annual_div + (as_float(inputs.get("annual_interest_musd")) or 0)
    coverage_months = (as_float(inputs.get("usd_reserve_musd")) or 0) / (annual_obligation / 12) if annual_obligation else None
    weekly_need = annual_obligation / 52 if annual_obligation else None
    weekly_sales = as_float(inputs.get("weekly_btc_sales_musd"))
    sale_ratio = weekly_sales / weekly_need if weekly_sales is not None and weekly_need else None
    common_shares = as_float(inputs.get("common_shares_outstanding_m"))
    sats_per_share = (as_float(inputs.get("mstr_btc_holdings")) or 0) * 1e8 / (common_shares * 1e6) if common_shares else None
    equity_mnav = enterprise_mnav = None
    if btc_px and mstr_px:
        btc_nav_musd = (as_float(inputs.get("mstr_btc_holdings")) or 0) * btc_px / 1e6
        mkt_cap_musd = (common_shares or 0) * mstr_px
        net_to_common = btc_nav_musd + (as_float(inputs.get("usd_reserve_musd")) or 0) + (as_float(inputs.get("cash_other_musd")) or 0) - (as_float(inputs.get("debt_face_musd")) or 0) - pref_total - (as_float(inputs.get("deferred_tax_liability_musd")) or 0)
        equity_mnav = mkt_cap_musd / net_to_common if net_to_common > 0 else None
        enterprise_mnav = (mkt_cap_musd + (as_float(inputs.get("debt_face_musd")) or 0) + pref_total - (as_float(inputs.get("usd_reserve_musd")) or 0) - (as_float(inputs.get("cash_other_musd")) or 0)) / btc_nav_musd if btc_nav_musd else None
    pref_dilution_flag = bool(pref_total > (as_float(inputs.get("prev_pref_notional_musd")) or 0) and equity_mnav and equity_mnav > (as_float(inputs.get("prev_mnav_equity")) or 0))
    strc_discount = 1 - strc_px / 100 if strc_px else None
    return {
        "equity_mnav": equity_mnav,
        "enterprise_mnav": enterprise_mnav,
        "common_valuation_gate_ok": bool(equity_mnav and equity_mnav <= 1 and not pref_dilution_flag),
        "capital_flywheel_gate_ok": bool(equity_mnav and enterprise_mnav and equity_mnav >= 1 and enterprise_mnav >= 1 and not pref_dilution_flag),
        "pref_dilution_flag": pref_dilution_flag,
        "coverage_months": coverage_months,
        "sale_ratio": sale_ratio,
        "sats_per_share": sats_per_share,
        "strc_discount": strc_discount,
    }


def assert_close(name: str, expected: Any, actual: Any, failures: list[str], tolerance: float = 1e-6) -> None:
    if isinstance(expected, bool) or isinstance(actual, bool):
        if bool(expected) != bool(actual):
            failures.append(f"{name}: 重算 {expected} != snapshot {actual}")
        return
    e = as_float(expected)
    a = as_float(actual)
    if e is None and a is None:
        return
    if e is None or a is None or abs(e - a) > tolerance * max(1, abs(e), abs(a)):
        failures.append(f"{name}: 重算 {e} != snapshot {a}")


def main() -> int:
    raw = load_json(RAW_PATH)
    snapshot = load_json(SNAPSHOT_PATH)
    market_universe = load_json(MARKET_UNIVERSE_PATH) if MARKET_UNIVERSE_PATH.exists() else {}
    observations = obs_map(raw)
    failures: list[str] = []
    warnings: list[str] = []
    degradations: list[str] = []
    evidence: list[str] = []
    structural_failures: list[str] = []
    structural_degradations: list[str] = []
    structural_evidence: list[str] = []

    if not market_universe:
        failures.append("market universe artifact missing")
    else:
        if market_universe.get("date") != snapshot.get("date"):
            failures.append(f"market universe date mismatch: {market_universe.get('date')} != {snapshot.get('date')}")
        universe_age = age_hours(market_universe.get("generated_at"))
        if universe_age is None:
            failures.append("market universe generated_at missing or invalid")
        elif universe_age > 8:
            failures.append(f"market universe stale {universe_age:.1f}h > 8h")
        universe_quality = market_universe.get("quality", {})
        if universe_quality.get("status") == "fail":
            failures.extend(f"market universe: {item}" for item in universe_quality.get("failures", []))
        elif universe_quality.get("status") == "degraded":
            degradations.extend(f"market universe: {item}" for item in universe_quality.get("degradations", []))
        elif universe_quality.get("status") != "pass":
            failures.append(f"market universe quality status invalid: {universe_quality.get('status')}")
        for symbol in ["BTC", "ETH", "HYPE", "SOL", "BNB", "XRP", "DOGE"]:
            asset = market_universe.get("assets", {}).get(symbol, {})
            if as_float(asset.get("price_usd")) is None:
                failures.append(f"market universe {symbol}: spot price missing")
            if int(asset.get("source_count") or 0) < 2:
                failures.append(f"market universe {symbol}: fewer than two spot sources")
            gap = as_float(asset.get("cross_source_gap"))
            if gap is not None and gap > 0.02:
                failures.append(f"market universe {symbol}: spot source gap {gap:.2%} > 2%")
            source_prices = [as_float(value) for value in asset.get("source_prices", {}).values()]
            source_prices = [value for value in source_prices if value is not None and value > 0]
            if len(source_prices) >= 2:
                assert_close(f"market universe {symbol} median spot", statistics.median(source_prices), asset.get("price_usd"), failures)
                expected_gap = (max(source_prices) - min(source_prices)) / statistics.mean(source_prices)
                assert_close(f"market universe {symbol} source gap", expected_gap, gap, failures)
            if len(source_prices) != int(asset.get("source_count") or 0):
                failures.append(f"market universe {symbol}: source_count does not match source_prices")
            for provider, observation in asset.get("source_observations", {}).items():
                source_age = age_hours(observation.get("as_of"))
                if source_age is None or source_age > 2:
                    failures.append(f"market universe {symbol} {provider}: source stale or timestamp missing")
                if provider in {"Binance", "OKX"}:
                    price_usdt = as_float(observation.get("price_usdt"))
                    usdt_usd = as_float(observation.get("usdt_usd"))
                    if price_usdt is None or usdt_usd is None:
                        failures.append(f"market universe {symbol} {provider}: USDT/USD normalization inputs missing")
                    else:
                        assert_close(f"market universe {symbol} {provider} USD normalization", price_usdt * usdt_usd, observation.get("price_usd"), failures)
                    usdt_age = age_hours(observation.get("usdt_usd_as_of"))
                    if usdt_age is None or usdt_age > 2:
                        failures.append(f"market universe {symbol} {provider}: USDT/USD normalization rate stale")
        for symbol in ["BTC", "ETH"]:
            derivative = market_universe.get("derivatives", {}).get(symbol, {})
            required_derivatives = {
                "cross-venue annualized funding": derivative.get("perpetual", {}).get("funding_annualized_median"),
                "dated futures basis": derivative.get("dated_future", {}).get("annualized_basis"),
                "options volatility proxy": derivative.get("options", {}).get("volatility_value"),
                "options put/call OI": derivative.get("options", {}).get("put_call_open_interest_ratio"),
            }
            for label, value in required_derivatives.items():
                if as_float(value) is None:
                    failures.append(f"market universe {symbol}: {label} missing")
            if int(derivative.get("perpetual", {}).get("funding_source_count") or 0) < 2:
                failures.append(f"market universe {symbol}: fewer than two perpetual funding venues")
            perpetual = derivative.get("perpetual", {})
            annualized_funding = []
            venue_names = perpetual.get("venues_used", [])
            for venue in venue_names:
                venue_data = perpetual.get(venue, {})
                rate = as_float(venue_data.get("funding_rate"))
                interval = as_float(venue_data.get("funding_interval_hours"))
                if rate is not None and interval not in (None, 0):
                    expected_annualized = rate * 24 / interval * 365
                    assert_close(f"market universe {symbol} {venue} funding annualization", expected_annualized, venue_data.get("funding_annualized"), failures)
                    annualized_funding.append(expected_annualized)
                venue_age = age_hours(venue_data.get("as_of"))
                if venue_age is None or venue_age > 2:
                    failures.append(f"market universe {symbol} {venue}: perpetual source stale or timestamp missing")
            if len(annualized_funding) != int(perpetual.get("funding_source_count") or 0):
                failures.append(f"market universe {symbol}: funding source count does not match venue data")
            if annualized_funding:
                assert_close(f"market universe {symbol} median annualized funding", statistics.median(annualized_funding), perpetual.get("funding_annualized_median"), failures)
            dated = derivative.get("dated_future", {})
            mark = as_float(dated.get("mark_price_usd"))
            index = as_float(dated.get("index_price_usd"))
            days = as_float(dated.get("days_to_delivery"))
            if mark is not None and index not in (None, 0) and days not in (None, 0):
                expected_basis = mark / index - 1
                assert_close(f"market universe {symbol} dated-futures basis", expected_basis, dated.get("basis"), failures)
                assert_close(f"market universe {symbol} annualized basis", expected_basis * 365 / days, dated.get("annualized_basis"), failures)
            dated_age = age_hours(dated.get("as_of"))
            if dated_age is None or dated_age > 2:
                failures.append(f"market universe {symbol}: dated-futures source stale or timestamp missing")
            if dated.get("provider") not in {"Deribit", "OKX"}:
                failures.append(f"market universe {symbol}: unsupported dated-futures provider")
            if dated.get("provider") == "OKX" and dated.get("price_basis") != "bid_ask_midpoint_else_last":
                failures.append(f"market universe {symbol}: OKX dated-futures price basis missing")
            options = derivative.get("options", {})
            if options.get("contracts_observed") != options.get("open_interest_observed_contracts"):
                failures.append(f"market universe {symbol}: options OI coverage incomplete")
            if options.get("contracts_observed") != options.get("volume_observed_contracts"):
                failures.append(f"market universe {symbol}: options volume coverage incomplete")
            options_age = age_hours(options.get("as_of"))
            volatility_age = age_hours(options.get("volatility_as_of"))
            if options_age is None or options_age > 2:
                failures.append(f"market universe {symbol}: options source stale or timestamp missing")
            if volatility_age is None or volatility_age > 3:
                failures.append(f"market universe {symbol}: options volatility source stale or timestamp missing")
            call_oi = as_float(options.get("call_open_interest_base"))
            put_oi = as_float(options.get("put_open_interest_base"))
            if call_oi not in (None, 0) and put_oi is not None:
                assert_close(f"market universe {symbol} put/call OI", put_oi / call_oi, options.get("put_call_open_interest_ratio"), failures)
            if options.get("contract_set") not in {"inverse_coin_margined_only", "okx_coin_margined_options"}:
                failures.append(f"market universe {symbol}: options contract coverage is not explicit")
            atm_components = options.get("atm_components", [])
            atm_values = [as_float(item.get("mark_iv_pct")) for item in atm_components]
            atm_values = [value for value in atm_values if value is not None]
            if len(atm_components) != 2 or {item.get("option_type") for item in atm_components} != {"C", "P"} or len(atm_values) != 2:
                failures.append(f"market universe {symbol}: ATM IV components are incomplete")
            else:
                assert_close(f"market universe {symbol} ATM IV mean", statistics.mean(atm_values), options.get("atm_implied_volatility"), failures)
            if options.get("provider") == "Deribit":
                if options.get("contract_set") != "inverse_coin_margined_only" or options.get("volatility_metric") != "deribit_dvol" or options.get("volatility_label") != "Deribit 隱含波動率指數":
                    failures.append(f"market universe {symbol}: Deribit options contract mapping mismatched")
                assert_close(f"market universe {symbol} Deribit DVOL mapping", options.get("dvol"), options.get("volatility_value"), failures)
            elif options.get("provider") == "OKX":
                if options.get("contract_set") != "okx_coin_margined_options" or options.get("volatility_metric") != "okx_atm_mark_iv_near_30d" or options.get("volatility_label") != "OKX 約 30 日 ATM 標記隱含波動率":
                    failures.append(f"market universe {symbol}: OKX options contract mapping mismatched")
                expected_filter = {"ct_type": "inverse", "settle_ccy": symbol, "inst_family": f"{symbol}-USD", "state": "live"}
                if options.get("contract_filter") != expected_filter:
                    failures.append(f"market universe {symbol}: OKX options contract filter mismatched")
                contract_counts = options.get("contract_type_counts", {})
                observed_ids = options.get("observed_contract_ids", [])
                observed_count = int(options.get("contracts_observed") or 0)
                if (
                    int(contract_counts.get("observed_inverse_contracts") or 0) != observed_count
                    or int(contract_counts.get("eligible_inverse_instruments") or 0) < observed_count
                    or int(contract_counts.get("excluded_non_inverse_instruments") or 0) < 1
                    or len(observed_ids) != observed_count
                    or len(set(observed_ids)) != observed_count
                    or any("_UM-" in str(contract_id) for contract_id in observed_ids)
                ):
                    failures.append(f"market universe {symbol}: OKX options contract-set purity failed")
                assert_close(f"market universe {symbol} OKX ATM IV mapping", options.get("atm_implied_volatility"), options.get("volatility_value"), failures)
            else:
                failures.append(f"market universe {symbol}: unsupported options provider")
        thesis = market_universe.get("btc_thesis", {})
        thesis_quality = thesis.get("quality", {})
        if thesis_quality.get("coverage_status") != "complete":
            structural_failures.append("BTC thesis layer incomplete: " + ", ".join(thesis_quality.get("missing", [])))
        structural_failures.extend(thesis_quality.get("failures", []))
        structural_degradations.extend(thesis_quality.get("degradations", []))
        if thesis_quality.get("execution_gate_eligible") is not False:
            structural_failures.append("BTC thesis quality must be explicitly ineligible for execution gates")

        gold = thesis.get("gold_monetization", {})
        btc_market_cap = as_float(gold.get("btc_market_cap_usd"))
        btc_supply = as_float(gold.get("btc_supply_used"))
        gold_price = as_float(gold.get("gold_price_proxy_usd_per_troy_oz"))
        gold_tonnes = as_float(gold.get("above_ground_gold_tonnes"))
        gold_value = as_float(gold.get("estimated_gold_market_value_usd"))
        if gold_price is not None and gold_tonnes is not None:
            assert_close("BTC thesis estimated gold market value", gold_price * gold_tonnes * TROY_OZ_PER_METRIC_TONNE, gold_value, structural_failures, tolerance=1e-8)
        if btc_market_cap is not None and gold_value not in (None, 0):
            assert_close("BTC thesis BTC/gold ratio", btc_market_cap / gold_value, gold.get("btc_to_gold_market_value_ratio"), structural_failures)
        if btc_supply not in (None, 0) and gold_value is not None:
            scenarios = gold.get("scenario_btc_price_usd", {})
            for key, share in {"gold_25pct": 0.25, "gold_50pct": 0.50, "gold_100pct": 1.0}.items():
                assert_close(f"BTC thesis {key} scenario", gold_value * share / btc_supply, scenarios.get(key), structural_failures, tolerance=1e-8)
        gold_age = age_hours(gold.get("gold_price_as_of"))
        if gold_age is None or gold_age < -0.25 or gold_age > 72:
            structural_degradations.append("BTC thesis gold-price proxy stale or timestamp missing")
        stock_year = int(gold.get("above_ground_stock_year") or 0)
        if stock_year < datetime.now(timezone.utc).year - 2:
            structural_degradations.append("BTC thesis World Gold Council stock estimate is older than two years")

        credit = thesis.get("digital_dollar_competition", {})
        stablecoin_supply = as_float(credit.get("stablecoin_supply_usd"))
        matched_supply = as_float(credit.get("stablecoin_supply_matched_cohort_usd"))
        matched_prior = as_float(credit.get("stablecoin_supply_matched_cohort_30d_ago_usd"))
        if matched_supply is not None and matched_prior not in (None, 0):
            assert_close("BTC thesis stablecoin matched-cohort 30d change", matched_supply / matched_prior - 1, credit.get("stablecoin_supply_30d_change"), structural_failures)
        matched_count = int(credit.get("stablecoin_30d_matched_count") or 0)
        unmatched_count = int(credit.get("stablecoin_30d_unmatched_count") or 0)
        total_count = int(credit.get("usd_stablecoin_count") or 0)
        if matched_count + unmatched_count != total_count or matched_count < 1:
            structural_failures.append("BTC thesis stablecoin matched-cohort counts are inconsistent")
        if btc_market_cap is not None and stablecoin_supply not in (None, 0):
            assert_close("BTC thesis BTC/stablecoin scale", btc_market_cap / stablecoin_supply, credit.get("btc_to_stablecoin_market_scale_ratio"), structural_failures)
            assert_close("BTC thesis digital anchor share", btc_market_cap / (btc_market_cap + stablecoin_supply), credit.get("btc_share_of_btc_plus_stablecoins"), structural_failures)
        if as_float(credit.get("rwa_protocol_tvl_usd")) is None or int(credit.get("rwa_protocol_count") or 0) < 1:
            structural_degradations.append("BTC thesis RWA protocol coverage missing")
        credit_age = age_hours(credit.get("as_of"))
        if credit_age is None or credit_age < -0.25 or credit_age > 8:
            structural_degradations.append("BTC thesis stablecoin/RWA retrieval stale or timestamp missing")

        company = thesis.get("public_company_adoption", {})
        company_holdings = as_float(company.get("observed_public_company_btc"))
        company_supply = as_float(company.get("btc_supply_used"))
        top_company_holdings = as_float(company.get("top_company_btc"))
        if company_holdings is not None and company_supply not in (None, 0):
            assert_close("BTC thesis public-company supply share", company_holdings / company_supply, company.get("share_of_btc_supply"), structural_failures)
        if top_company_holdings is not None and company_holdings not in (None, 0):
            assert_close("BTC thesis top-company concentration", top_company_holdings / company_holdings, company.get("top_company_share_of_observed_holdings"), structural_failures)
        company_age = age_hours(company.get("as_of"))
        if company_age is None or company_age < -0.25 or company_age > 8:
            structural_degradations.append("BTC thesis public-company treasury retrieval stale or timestamp missing")

        security = thesis.get("security_consensus", {})
        hashrate = as_float(security.get("hashrate_ths"))
        hashrate_30d = as_float(security.get("hashrate_30d_ago_ths"))
        hashrate_high = as_float(security.get("hashrate_90d_high_ths"))
        if hashrate is not None and hashrate_30d not in (None, 0):
            assert_close("BTC thesis hashrate 30d change", hashrate / hashrate_30d - 1, security.get("hashrate_30d_change"), structural_failures)
        if hashrate is not None and hashrate_high not in (None, 0):
            assert_close("BTC thesis hashrate vs 90d high", hashrate / hashrate_high, security.get("hashrate_vs_90d_high"), structural_failures)
        security_age = age_hours(security.get("as_of"))
        if security_age is None or security_age < -0.25 or security_age > 72:
            structural_degradations.append("BTC thesis hashrate history stale or timestamp missing")

        sovereign = thesis.get("sovereign_credit_competition", {})
        debt_age = age_days(sovereign.get("us_federal_debt_to_gdp_as_of"))
        real_yield_age = age_days(sovereign.get("us_10y_real_yield_as_of"))
        if debt_age is None or debt_age < 0 or debt_age > 240:
            structural_degradations.append("BTC thesis U.S. debt/GDP stale or timestamp missing")
        if real_yield_age is None or real_yield_age < 0 or real_yield_age > 10:
            structural_degradations.append("BTC thesis U.S. 10-year real yield stale or timestamp missing")
        if thesis.get("unmeasured_falsifier", {}).get("status") != "unknown_no_complete_public_dataset":
            structural_failures.append("BTC thesis must preserve unknown global BTC collateral stock")
        structural_evidence.append(
            f"BTC thesis gold_ratio={gold.get('btc_to_gold_market_value_ratio')} "
            f"stablecoin_supply={credit.get('stablecoin_supply_usd')} company_supply_share={company.get('share_of_btc_supply')}"
        )
        if len(market_universe.get("sources", [])) < 20:
            degradations.append("market universe: fewer than 20 traceable source records")
        evidence.append(
            f"market universe quality={universe_quality.get('status')} score={universe_quality.get('score_0_100')} "
            f"sources={len(market_universe.get('sources', []))} age_hours={universe_age:.2f}" if universe_age is not None else "market universe age unavailable"
        )

    for required in ["btc_usd_coingecko", "btc_usd_coinbase", "eth_usd_coingecko", "eth_usd_coinbase", "mstr_usd_yahoo"]:
        item = observations.get(required)
        if not item or not item.get("ok"):
            failures.append(f"必要來源失敗: {required}")
        else:
            evidence.append(f"{required}={item.get('value')} from {item.get('source')}")

    check_cross_source(
        "BTC spot",
        as_float(observations.get("btc_usd_coingecko", {}).get("value")),
        as_float(observations.get("btc_usd_coinbase", {}).get("value")),
        0.015,
        failures,
        warnings,
    )
    check_cross_source(
        "ETH spot",
        as_float(observations.get("eth_usd_coingecko", {}).get("value")),
        as_float(observations.get("eth_usd_coinbase", {}).get("value")),
        0.015,
        failures,
        warnings,
    )
    check_observation_freshness("BTC MVRV", observations.get("btc_mvrv_current"), 3, 7, failures, degradations, evidence)
    strategy_holdings = observations.get("mstr_sec_btc_holdings_latest") or observations.get("mstr_strategy_btc_holdings")
    check_observation_freshness("Strategy BTC holdings", strategy_holdings, 14, 45, failures, degradations, evidence)
    sec_sales = observations.get("mstr_sec_rolling_7d_sales_musd")
    if sec_sales and sec_sales.get("ok"):
        check_observation_freshness("Strategy SEC 7d sales", sec_sales, 2, 7, failures, degradations, evidence)
    check_observation_freshness("BMNR treasury holdings", observations.get("bmnr_eth_holdings"), 14, 30, failures, degradations, evidence)
    for ticker in ["mstr", "bmnr", "strc"]:
        check_equity_cross_source(
            ticker,
            observations.get(f"{ticker}_usd_yahoo"),
            observations.get(f"{ticker}_usd_nasdaq"),
            failures,
            degradations,
            warnings,
            evidence,
        )

    latest_form = str(observations.get("mstr_sec_latest_form", {}).get("value") or "")
    if latest_form:
        evidence.append(f"SEC latest form={latest_form}")
    else:
        degradations.append("SEC submissions 不可用；資本結構 manual inputs 需人工覆核")
    if latest_form and latest_form not in {"8-K", "10-K", "10-Q", "S-3ASR", "424B5", "4", "144"}:
        warnings.append(f"SEC 最新表單型別非核心清單: {latest_form}")

    metrics = snapshot.get("metrics", {}).get("mstr_metrics", {})
    numeric_ranges = {
        "equity_mnav": (0, 20),
        "enterprise_mnav": (0, 20),
        "coverage_months": (0, 120),
        "sale_ratio": (0, 100),
        "sats_per_share": (0, 10_000_000),
        "strc_discount": (-1, 1),
    }
    for key, (low, high) in numeric_ranges.items():
        value = as_float(metrics.get(key))
        if value is None:
            failures.append(f"{key}: 缺值")
            continue
        if not low <= value <= high:
            failures.append(f"{key}: {value} 超出合理範圍 {low}..{high}")

    recomputed = recompute_metrics(snapshot)
    for key, expected in recomputed.items():
        assert_close(key, expected, metrics.get(key), failures)

    radar = snapshot.get("metrics", {}).get("market_radar", {})
    if as_float(radar.get("fear_greed")) is None:
        degradations.append("Fear & Greed missing")
    if as_float(radar.get("btc_fee_fastest_sat_vb")) is None:
        degradations.append("mempool fee proxy missing")
    if as_float(radar.get("treasury_avg_bill_rate_pct")) is None:
        degradations.append("macro funding proxy missing")
    etf_status = str(radar.get("etf_flow_status") or "")
    if etf_status == "cross_source_verified":
        evidence.append("ETF flow automated and cross-source verified")
    elif etf_status == "automated_third_party_single_source":
        degradations.append("ETF flow automated from third-party single source; not eligible as hard trigger until cross-source verified")
    else:
        degradations.append("ETF flow unavailable; not eligible as hard trigger")
    btc_required = ["btc_mvrv_current", "btc_200dma", "btc_50dma", "btc_200wma", "btc_drawdown_1y_pct", "btc_return_7d_pct", "btc_return_30d_pct"]
    missing_btc = [key for key in btc_required if as_float(radar.get(key)) is None]
    if missing_btc:
        degradations.append("BTC standard inputs missing: " + ", ".join(missing_btc))
    btc_standard = snapshot.get("metrics", {}).get("btc_standard", {})
    if not btc_standard.get("regime") or as_float(btc_standard.get("score")) is None:
        failures.append("BTC standard: missing regime or score")
    else:
        evidence.append(f"BTC standard={btc_standard.get('regime')} score={btc_standard.get('score')}")
        if "MSTR 資本壓力" in btc_standard.get("dimensions", {}):
            failures.append("BTC standard: vehicle risk must not be mixed into BTC market dimensions")
        if any(key in btc_standard.get("signals", {}) for key in ["mstr_sale_pressure_ratio", "strc_discount"]):
            failures.append("BTC standard: vehicle-risk signals must remain outside BTC signal inputs")
        if as_float(btc_standard.get("dimension_weights", {}).get("ETF 邊際買盤")) not in (None, 0.5):
            failures.append("BTC standard: single-source ETF flow weight must remain capped at 0.5")
        coverage_ratio = as_float(btc_standard.get("data_quality", {}).get("coverage_ratio"))
        if coverage_ratio is None or coverage_ratio < 0.8:
            degradations.append(f"BTC standard coverage ratio insufficient: {coverage_ratio}")

    bmnr_metrics = snapshot.get("metrics", {}).get("bmnr_metrics", {})
    bmnr_required = [
        "eth_holdings",
        "btc_holdings",
        "cash_marketable_musd",
        "beast_stake_musd",
        "eightco_stake_musd",
        "bottom_up_gross_treasury_musd",
        "buyback_adjusted_shares_estimate_m",
        "market_cap_to_gross_treasury",
        "gross_treasury_value_per_share",
    ]
    missing_bmnr = [key for key in bmnr_required if as_float(bmnr_metrics.get(key)) is None]
    if missing_bmnr:
        degradations.append("BMNR treasury analytics missing: " + ", ".join(missing_bmnr))
    else:
        bmnr_ratio = as_float(bmnr_metrics.get("market_cap_to_gross_treasury"))
        if bmnr_ratio is not None and not 0 < bmnr_ratio < 10:
            failures.append(f"BMNR market-cap/gross-treasury ratio out of range: {bmnr_ratio}")
        bmnr_gap = as_float(bmnr_metrics.get("reported_total_crosscheck_gap"))
        if bmnr_gap is not None and bmnr_gap > 0.10:
            degradations.append(f"BMNR bottom-up vs reported holdings gap {bmnr_gap:.2%} > 10%; other assets or marks require review")
        evidence.append(
            f"BMNR gross treasury={bmnr_metrics.get('bottom_up_gross_treasury_musd')}m "
            f"market_cap_to_gross={bmnr_metrics.get('market_cap_to_gross_treasury')} "
            f"as_of={bmnr_metrics.get('holdings_as_of')}"
        )

    manual = snapshot.get("metrics", {}).get("manual_inputs", {})
    provenance = snapshot.get("metrics", {}).get("manual_input_provenance", {})
    fields = provenance.get("fields", {})
    manual_risk_keys = ["mstr_btc_holdings", "usd_reserve_musd", "cash_other_musd", "debt_face_musd", "annual_interest_musd", "preferred", "weekly_btc_sales_musd", "common_shares_outstanding_m", "deferred_tax_liability_musd", "prev_pref_notional_musd", "prev_mnav_equity"]
    manual_fields = [key for key in manual_risk_keys if fields.get(key, {}).get("source_type") in {"manual", None}]
    missing_required_fields = [key for key in manual_risk_keys if fields.get(key, {}).get("source_type") == "missing_required"]
    if missing_required_fields:
        failures.append("required capital-structure sources missing: " + ", ".join(missing_required_fields))
    if manual_fields:
        degradations.append("manual capital-structure inputs: " + ", ".join(manual_fields))
    if provenance.get("status") != "automated":
        degradations.append(f"capital-structure provenance status={provenance.get('status', 'missing')}")
    for field_name, field in fields.items():
        source_type = str(field.get("source_type") or "")
        if not source_type.startswith("official_"):
            continue
        if field_name == "mstr_btc_holdings":
            continue
        age = age_days(field.get("as_of"))
        if age is None:
            degradations.append(f"{field_name}: official provenance missing as_of")
            continue
        warn_after, fail_after = (14, 45) if field_name == "mstr_btc_holdings" else (150, 240)
        if field_name == "usd_reserve_musd":
            warn_after, fail_after = 30, 120
        if field_name == "common_shares_outstanding_m":
            warn_after, fail_after = 45, 120
        if field_name == "weekly_btc_sales_musd":
            warn_after, fail_after = 2, 7
        if age > fail_after:
            failures.append(f"{field_name}: official input stale {age} days > {fail_after}")
        elif age > warn_after:
            degradations.append(f"{field_name}: official input stale {age} days > {warn_after}")
    sec_facts = snapshot.get("metrics", {}).get("sec_companyfacts", {})
    sec_diluted = as_float(sec_facts.get("diluted_shares_m"))
    effective_diluted = as_float(manual.get("diluted_shares_m"))
    if sec_diluted is not None and effective_diluted is not None and pct_gap(sec_diluted, effective_diluted) > 0.01:
        failures.append(f"diluted_shares_m: effective input differs from SEC companyfacts by {pct_gap(sec_diluted, effective_diluted):.2%}")
    if as_float(manual.get("weekly_btc_sales_musd")) is None and metrics.get("contract_red_light") is not True:
        failures.append("weekly_btc_sales_musd unknown must fail closed for MSTR contract gate")

    failures = unique(failures)
    degradations = unique(degradations)
    warnings = unique(warnings)
    evidence = unique(evidence)
    structural_failures = unique(structural_failures)
    structural_degradations = unique(structural_degradations)
    structural_evidence = unique(structural_evidence)
    status = "fail" if failures else ("degraded" if degradations or warnings else "pass")
    structural_status = "fail" if structural_failures else ("degraded" if structural_degradations else "pass")
    report = {
        "schema": 1,
        "agent": "daily-data-verifier",
        "verified_at": now_iso(),
        "date": snapshot.get("date"),
        "snapshot_generated_at": snapshot.get("generated_at"),
        "market_universe_generated_at": market_universe.get("generated_at"),
        "status": status,
        "status_scope": "execution_and_decision_inputs_only",
        "failures": failures,
        "degradations": degradations,
        "warnings": warnings,
        "evidence": evidence,
        "structural_context_quality": {
            "status": structural_status,
            "scope": "structural_context_only",
            "execution_gate_eligible": False,
            "failures": structural_failures,
            "degradations": structural_degradations,
            "evidence": structural_evidence,
            "verification_scope": "formula integrity, timestamps and declared source semantics; not an independent reconstruction of every upstream dataset",
        },
        "policy": {
            "btc_cross_source_max_gap": "1.5%",
            "eth_cross_source_max_gap": "1.5%",
            "equity_cross_source_max_gap_same_basis": "2%",
            "equity_mismatched_quote_basis": "degraded, not fail",
            "daily_equity_snapshot_basis": "Yahoo regular-market close preferred; Nasdaq quote is backup/freshness evidence",
            "required_sources": ["CoinGecko BTC/ETH", "Coinbase BTC/ETH", "Yahoo Finance MSTR"],
            "degraded_if_missing": ["Nasdaq backup quotes", "SEC EDGAR submissions", "automated capital-structure inputs", "cross-source ETF flow verification", "BTC MVRV ratio"],
            "btc_standard_required_inputs": ["BTC spot cross-source", "BTC 50/200DMA", "BTC MVRV ratio", "Fear & Greed", "ETF flow context"],
            "freshness_limits": {"BTC MVRV": "warn >3d, fail >7d", "Strategy holdings": "warn >14d, fail >45d", "Strategy 7d sales disclosure": "warn >2d, fail >7d", "USD reserve": "warn >30d, fail >120d", "MSTR common shares": "warn >45d, fail >120d", "BMNR holdings": "warn >14d, fail >30d"},
            "not_hard_triggers": ["single-source ETF flow", "realized loss without stable free API", "Google Trends without official unauthenticated API", "macro calendar without official free event API"],
            "market_universe": {
                "update_target": "every 4 hours",
                "fail_if_stale": ">8h",
                "tracked_assets": ["BTC", "ETH", "HYPE", "SOL", "BNB", "XRP", "DOGE"],
                "derivatives": ["Bybit/OKX/Hyperliquid and available Binance perpetuals", "Deribit near-90-day dated futures with OKX fallback", "CME Yahoo proxy", "Deribit DVOL/options with labeled OKX ATM-IV/options fallback"],
                "coverage_rule": "venue observations remain partial-market context; unknown data never becomes zero",
            },
        },
    }
    write_json(REPORT_PATH, report)
    print(json.dumps({"status": status, "failures": len(failures), "degradations": len(degradations), "warnings": len(warnings)}, ensure_ascii=False))
    return 0 if status != "fail" else 1


if __name__ == "__main__":
    sys.exit(main())
