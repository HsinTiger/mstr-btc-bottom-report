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
ETF_REQUIRED_ROSTER = {
    "BTC": {"ARKB", "BITB", "BRRR", "BTC", "BTCO", "BTCW", "DEFI", "EZBC", "FBTC", "GBTC", "HODL", "IBIT", "MSBT"},
    "ETH": {"ETH", "ETHA", "ETHE", "ETHV", "ETHW", "EZET", "FETH", "QETH"},
}
ETF_MAX_ABS_DAILY_FUND_FLOW_USD = 50_000_000_000
ETF_MAX_GROSS_DAILY_FLOW_USD = 100_000_000_000
ETF_COMPONENT_SUM_ABSOLUTE_TOLERANCE_USD = 500_000
ETF_COMPONENT_SUM_RELATIVE_TOLERANCE = 0.001


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


def classify_verification_status(failures: list[str], degradations: list[str]) -> str:
    return "fail" if failures else "degraded" if degradations else "pass"


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


def check_spot_source_pool(
    label: str,
    observation_names: list[str],
    observations: dict[str, Any],
    snapshot_price: Any,
    price_basis: dict[str, Any],
    failures: list[str],
    warnings: list[str],
    evidence: list[str],
    threshold: float = 0.015,
) -> None:
    available: list[tuple[str, dict[str, Any], float]] = []
    for name in observation_names:
        item = observations.get(name)
        value = as_float((item or {}).get("value"))
        if item and item.get("ok") and value is not None and value > 0:
            source_age = age_hours(item.get("as_of"))
            if source_age is None or source_age < -0.25 or source_age > 2:
                failures.append(f"{label}: {name} 時間戳不可信或逾時")
                continue
            available.append((name, item, value))
    if len(available) < 2:
        failures.append(f"{label}: 可用來源僅 {len(available)} 個，至少需要 2 個")
        return
    values = [value for _, _, value in available]
    median = statistics.median(values)
    gap = (max(values) - min(values)) / statistics.mean(values)
    assert_close(f"{label} source-pool median", median, snapshot_price, failures)
    assert_close(f"{label} recorded cross-source gap", gap, price_basis.get("cross_source_gap"), failures)
    if int(price_basis.get("source_count") or 0) != len(available):
        failures.append(f"{label}: price_basis source_count 與可用來源不一致")
    if set(price_basis.get("selected_observations") or []) != {name for name, _, _ in available}:
        failures.append(f"{label}: price_basis 未記錄實際使用的來源池")
    if gap > threshold:
        failures.append(f"{label}: 來源池價差 {gap:.2%} > {threshold:.2%}")
    elif gap > threshold / 2:
        warnings.append(f"{label}: 來源池價差 {gap:.2%} 接近門檻")
    providers = ", ".join(str(item.get("source")) for _, item, _ in available)
    evidence.append(f"{label} median={median} sources={len(available)} [{providers}] gap={gap:.2%}")


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
    preferred_class_total = sum(as_float(item.get("notional_musd")) or 0 for item in preferred.values())
    pref_total = max(preferred_class_total, as_float(inputs.get("preferred_aggregate_musd")) or 0)
    maximum_preferred_rate = max((as_float(item.get("rate")) or 0 for item in preferred.values()), default=0)
    annual_div = sum((as_float(item.get("notional_musd")) or 0) * (as_float(item.get("rate")) or 0) for item in preferred.values()) + max(pref_total - preferred_class_total, 0) * maximum_preferred_rate
    annual_obligation = annual_div + (as_float(inputs.get("annual_interest_musd")) or 0) + (as_float(inputs.get("other_debt_annual_service_musd")) or 0)
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


def normalized_gap(first: Any, second: Any, *, scale_floor: float = 0.0) -> float | None:
    first_value = as_float(first)
    second_value = as_float(second)
    if first_value is None or second_value is None:
        return None
    denominator = max((abs(first_value) + abs(second_value)) / 2, scale_floor)
    return abs(first_value - second_value) / denominator if denominator else 0.0


def recompute_etf_validation(inputs: dict[str, Any], asset: str | None = None) -> dict[str, Any]:
    errors: list[str] = []
    components_raw = inputs.get("canonical_components_usd")
    if not isinstance(components_raw, dict):
        components_raw = {}
        errors.append("canonical components missing")
    components = {str(key): as_float(value) for key, value in components_raw.items()}
    if any(value is None for value in components.values()):
        errors.append("canonical components contain non-numeric values")
    numeric_components = {key: value for key, value in components.items() if value is not None}
    gross_flow = sum(abs(value) for value in numeric_components.values())
    component_total = sum(numeric_components.values())
    reported_total = as_float(inputs.get("canonical_total_usd"))
    total_difference = abs(component_total - reported_total) if reported_total is not None else None
    total_tolerance = max(
        ETF_COMPONENT_SUM_ABSOLUTE_TOLERANCE_USD,
        ETF_COMPONENT_SUM_RELATIVE_TOLERANCE * max(abs(component_total), abs(reported_total or 0)),
    )
    total_reconciled = total_difference is not None and total_difference <= total_tolerance
    expected_tickers_raw = inputs.get("expected_tickers")
    expected_tickers = {str(ticker) for ticker in expected_tickers_raw} if isinstance(expected_tickers_raw, list) else set()
    if not expected_tickers:
        errors.append("expected fund roster missing")
    if asset in ETF_REQUIRED_ROSTER and not ETF_REQUIRED_ROSTER[asset].issubset(expected_tickers):
        errors.append("expected fund roster is below the governed minimum")
    expected_count = len(expected_tickers)
    if int(as_float(inputs.get("expected_ticker_count")) or 0) != expected_count:
        errors.append("expected fund roster count mismatch")
    component_count = sum(ticker in numeric_components for ticker in expected_tickers)
    completeness = component_count / expected_count if expected_count else None

    official_ticker = str(inputs.get("official_ticker") or "")
    official_component = numeric_components.get(official_ticker)
    recorded_official_component = as_float(inputs.get("official_component_usd"))
    if official_component is None or recorded_official_component != official_component:
        errors.append("official component does not match canonical roster")
    official_proxy = as_float(inputs.get("official_proxy_usd"))
    official_gap = normalized_gap(official_component, official_proxy, scale_floor=100_000_000)
    official_coverage = abs(official_component) / gross_flow if official_component is not None and gross_flow else None

    backup = inputs.get("backup_sample") if isinstance(inputs.get("backup_sample"), dict) else {}
    matched_tickers = backup.get("matched_tickers") if isinstance(backup.get("matched_tickers"), list) else []
    canonical_values = backup.get("canonical_values_usd") if isinstance(backup.get("canonical_values_usd"), dict) else {}
    backup_values = backup.get("backup_values_usd") if isinstance(backup.get("backup_values_usd"), dict) else {}
    same_date = bool(inputs.get("canonical_as_of") and backup.get("as_of") == inputs.get("canonical_as_of"))
    weighted_difference = 0.0
    weighted_reference = 0.0
    component_gaps: dict[str, float] = {}
    matched_canonical_gross = 0.0
    validation_type = str(backup.get("validation_type") or "")
    for ticker in matched_tickers:
        canonical_value = reported_total if validation_type == "same_date_aggregate_total" and ticker == "TOTAL" else numeric_components.get(str(ticker))
        recorded_canonical = as_float(canonical_values.get(ticker))
        backup_value = as_float(backup_values.get(ticker))
        if canonical_value is None or backup_value is None or recorded_canonical != canonical_value:
            errors.append(f"backup sample {ticker} is not reconstructable")
            continue
        gap = normalized_gap(canonical_value, backup_value, scale_floor=100_000_000)
        if gap is None:
            errors.append(f"backup sample {ticker} gap missing")
            continue
        component_gaps[str(ticker)] = gap
        weighted_difference += abs(canonical_value - backup_value)
        weighted_reference += (abs(canonical_value) + abs(backup_value)) / 2
        matched_canonical_gross += abs(canonical_value)
    if backup.get("provider") and not matched_tickers:
        errors.append("backup provider has no matched fund")
    backup_weighted_gap = weighted_difference / max(weighted_reference, 100_000_000) if component_gaps else None
    backup_max_gap = max(component_gaps.values()) if component_gaps else None
    backup_coverage = 1.0 if validation_type == "same_date_aggregate_total" and component_gaps else matched_canonical_gross / gross_flow if gross_flow else None
    amount_sanity_errors: list[str] = []
    if any(abs(value) > ETF_MAX_ABS_DAILY_FUND_FLOW_USD for value in numeric_components.values()):
        amount_sanity_errors.append("canonical single-fund daily flow exceeds sanity bound")
    if gross_flow > ETF_MAX_GROSS_DAILY_FLOW_USD:
        amount_sanity_errors.append("canonical gross daily flow exceeds sanity bound")
    if official_proxy is None or abs(official_proxy) > ETF_MAX_ABS_DAILY_FUND_FLOW_USD:
        amount_sanity_errors.append("official major-fund proxy missing or exceeds sanity bound")
    if any((value := as_float(raw_value)) is None or abs(value) > ETF_MAX_ABS_DAILY_FUND_FLOW_USD for raw_value in backup_values.values()):
        amount_sanity_errors.append("backup sample amount missing or exceeds sanity bound")
    source_count = 1 + int(official_proxy is not None) + int(bool(backup.get("provider") and component_gaps and same_date))
    return {
        "errors": errors,
        "canonical_component_sum_usd": component_total,
        "reported_canonical_total_usd": reported_total,
        "canonical_total_difference_usd": total_difference,
        "canonical_total_tolerance_usd": total_tolerance,
        "canonical_total_reconciled": total_reconciled,
        "gross_component_flow_usd": gross_flow,
        "component_count": component_count,
        "component_completeness": completeness,
        "official_gap": official_gap,
        "official_coverage": official_coverage,
        "backup_same_date": same_date,
        "backup_weighted_gap": backup_weighted_gap,
        "backup_max_gap": backup_max_gap,
        "backup_coverage": backup_coverage,
        "amount_sanity_pass": not amount_sanity_errors,
        "amount_sanity_errors": amount_sanity_errors,
        "validation_source_count": source_count,
    }


def recompute_dat_validation(item: dict[str, Any]) -> dict[str, Any]:
    validation = item.get("validation") if isinstance(item.get("validation"), dict) else {}
    base_provider = str(item.get("total_holdings_base_provider") or "")
    base_total = as_float(item.get("total_holdings_base"))
    errors: list[str] = []
    weighted_difference = 0.0
    weighted_reference = 0.0
    matched_base_holdings = 0.0
    maximum_company_gap = 0.0
    providers: set[str] = set()
    comparison_count = 0
    for comparison in validation.get("comparisons", []):
        values_raw = comparison.get("provider_values") if isinstance(comparison.get("provider_values"), dict) else {}
        values = {
            str(provider): value
            for provider, raw_value in values_raw.items()
            if (value := as_float(raw_value)) is not None and value >= 0
        }
        if base_provider not in values or len(values) < 2:
            errors.append(f"{comparison.get('symbol')}: comparison lacks base plus independent source")
            continue
        base_value = values[base_provider]
        consensus_values = {
            provider: value
            for provider, value in values.items()
            if provider == base_provider
            or abs(value - base_value) / max(statistics.median([value, base_value]), 1) <= 0.05
        }
        if len(consensus_values) < 2:
            errors.append(f"{comparison.get('symbol')}: no independent value agrees with the base within 5%")
            continue
        outlier_values = {provider: value for provider, value in values.items() if provider not in consensus_values}
        recorded_consensus = comparison.get("consensus_provider_values") if isinstance(comparison.get("consensus_provider_values"), dict) else {}
        recorded_outliers = comparison.get("excluded_outlier_provider_values") if isinstance(comparison.get("excluded_outlier_provider_values"), dict) else {}
        if recorded_consensus != consensus_values or recorded_outliers != outlier_values:
            errors.append(f"{comparison.get('symbol')}: consensus/outlier classification mismatch")
        reference = statistics.median(consensus_values.values())
        company_gap = (max(consensus_values.values()) - min(consensus_values.values())) / reference if reference else 0.0
        recorded_median = as_float(comparison.get("median_holdings"))
        if recorded_median is None or abs(recorded_median - reference) > 1e-9 * max(1, abs(reference)):
            errors.append(f"{comparison.get('symbol')}: recorded median mismatch")
        recorded_gap = as_float(comparison.get("max_relative_gap"))
        if recorded_gap is None or abs(recorded_gap - company_gap) > 1e-9 * max(1, abs(company_gap)):
            errors.append(f"{comparison.get('symbol')}: recorded company gap mismatch")
        weighted_difference += max(consensus_values.values()) - min(consensus_values.values())
        weighted_reference += reference
        matched_base_holdings += base_value
        maximum_company_gap = max(maximum_company_gap, company_gap)
        providers.update(consensus_values)
        comparison_count += 1
    weighted_gap = weighted_difference / weighted_reference if weighted_reference else None
    coverage = matched_base_holdings / base_total if base_total else None
    passed = bool(
        len(providers) >= 2
        and comparison_count >= 2
        and validation.get("official_overlay_complete") is True
        and coverage is not None
        and coverage >= 0.60
        and weighted_gap is not None
        and weighted_gap <= 0.01
        and maximum_company_gap <= 0.05
    )
    return {
        "errors": errors,
        "status": "representative_cross_source_verified" if passed else "quorum_failed",
        "provider_count": len(providers),
        "providers": sorted(providers),
        "matched_company_count": comparison_count,
        "matched_base_holdings": matched_base_holdings,
        "representative_coverage_ratio": coverage,
        "weighted_cross_source_gap": weighted_gap,
        "maximum_company_gap": maximum_company_gap if comparison_count else None,
        "excluded_outlier_count": sum(
            len(comparison.get("excluded_outlier_provider_values", {}))
            for comparison in validation.get("comparisons", [])
            if isinstance(comparison.get("excluded_outlier_provider_values"), dict)
        ),
    }


def recompute_dat_official_overlay(item: dict[str, Any], official_values: dict[str, float]) -> dict[str, Any]:
    validation = item.get("validation") if isinstance(item.get("validation"), dict) else {}
    base_provider = str(item.get("total_holdings_base_provider") or "")
    base_total = as_float(item.get("total_holdings_base"))
    comparisons = {
        str(comparison.get("symbol")): comparison
        for comparison in validation.get("comparisons", [])
        if comparison.get("symbol")
    }
    errors: list[str] = []
    adjustment = 0.0
    for symbol, official_value in official_values.items():
        base_value = as_float(comparisons.get(symbol, {}).get("provider_values", {}).get(base_provider))
        if base_value is None:
            errors.append(f"{symbol}: selected base value missing from DAT comparison evidence")
            continue
        adjustment += official_value - base_value
    expected_total = base_total + adjustment if base_total is not None and not errors else None
    return {
        "errors": errors,
        "official_overlay_adjustment": adjustment,
        "expected_total_holdings": expected_total,
    }


def recompute_sector_validation(item: dict[str, Any]) -> dict[str, Any]:
    observations = item.get("source_observations") if isinstance(item.get("source_observations"), dict) else {}
    errors: list[str] = []
    changes = [as_float(observation.get("change_24h")) for observation in observations.values()]
    changes = [value for value in changes if value is not None]
    market_caps = [as_float(observation.get("market_cap_usd")) for observation in observations.values()]
    market_caps = [value for value in market_caps if value is not None]
    volumes = [as_float(observation.get("volume_24h_usd")) for observation in observations.values()]
    volumes = [value for value in volumes if value is not None]
    gap = max(changes) - min(changes) if len(changes) >= 2 else None
    if len(changes) < 2:
        errors.append("fewer than two complete return sources")
    if len(market_caps) < 2 or len(volumes) < 2:
        errors.append("fewer than two market-cap or volume sources")
    if gap is None or gap > 0.01:
        errors.append("cross-source return gap exceeds 1 percentage point")
    if len(item.get("constituents", [])) != 5 or item.get("basket_version") != "fixed-basket-v1":
        errors.append("sector basket roster or version mismatch")
    for provider, observation in observations.items():
        source_age = age_hours(observation.get("as_of"))
        if source_age is None or source_age < -0.25 or source_age > 0.5:
            errors.append(f"{provider} timestamp outside freshness window")
    if not errors:
        assert_close("sector median return", statistics.median(changes), item.get("change_24h"), errors)
        assert_close("sector median market cap", statistics.median(market_caps), item.get("market_cap_usd"), errors)
        assert_close("sector median volume", statistics.median(volumes), item.get("volume_24h_usd"), errors)
        assert_close("sector source gap", gap, item.get("cross_source_gap"), errors)
    return {"errors": errors, "source_count": len(changes), "gap": gap}


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

    if raw.get("date") != snapshot.get("date"):
        failures.append(f"raw/snapshot date mismatch: {raw.get('date')} != {snapshot.get('date')}")
    if not raw.get("batch_id") or raw.get("batch_id") != snapshot.get("batch_id"):
        failures.append("raw observations and snapshot are not bound to the same batch_id")
    if raw.get("generated_at") != snapshot.get("generated_at"):
        failures.append("raw observations and snapshot generated_at mismatch")

    if not market_universe:
        failures.append("market universe artifact missing")
    else:
        if market_universe.get("date") != snapshot.get("date"):
            failures.append(f"market universe date mismatch: {market_universe.get('date')} != {snapshot.get('date')}")
        if market_universe.get("snapshot_generated_at") != snapshot.get("generated_at"):
            failures.append("market universe is not bound to the current daily snapshot")
        if market_universe.get("raw_generated_at") != raw.get("generated_at"):
            failures.append("market universe is not bound to the current raw observations")
        if market_universe.get("source_batch_id") != snapshot.get("batch_id") or market_universe.get("raw_batch_id") != raw.get("batch_id"):
            failures.append("market universe source batch lineage mismatch")
        universe_age = age_hours(market_universe.get("generated_at"))
        if universe_age is None:
            failures.append("market universe generated_at missing or invalid")
        elif universe_age > 3:
            failures.append(f"market universe stale {universe_age:.1f}h > 3h")
        universe_quality = market_universe.get("quality", {})
        if universe_quality.get("status") == "fail":
            failures.extend(f"market universe: {item}" for item in universe_quality.get("failures", []))
        elif universe_quality.get("status") == "degraded":
            degradations.extend(f"market universe: {item}" for item in universe_quality.get("degradations", []))
        elif universe_quality.get("status") != "pass":
            failures.append(f"market universe quality status invalid: {universe_quality.get('status')}")
        quality_checks = universe_quality.get("checks")
        validation_summary = universe_quality.get("validation_summary", {})
        if not isinstance(quality_checks, list) or not quality_checks:
            failures.append("market universe field-level quality checks missing")
        else:
            calculated = {
                "total": len(quality_checks),
                "passed": sum(item.get("status") == "pass" for item in quality_checks),
                "degraded": sum(item.get("status") == "degraded" for item in quality_checks),
                "failed": sum(item.get("status") == "fail" for item in quality_checks),
            }
            for key, expected in calculated.items():
                if int(validation_summary.get(key) or 0) != expected:
                    failures.append(f"market universe validation_summary {key} mismatch")
            if any(item.get("core") and item.get("status") == "fail" for item in quality_checks):
                failures.append("market universe contains failed core field checks")
        if not isinstance(universe_quality.get("source_incidents"), list):
            failures.append("market universe source_incidents contract missing")
        freshness_contract = universe_quality.get("freshness_contract", {})
        if as_float(freshness_contract.get("artifact_max_age_hours")) != 3:
            failures.append("market universe artifact freshness contract must be 3 hours")
        if as_float(freshness_contract.get("volatility_source_max_lag_hours")) != 3:
            failures.append("market universe volatility lag contract must be 3 hours relative to generated_at")
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
        for sector, item in market_universe.get("sectors", {}).items():
            sector_check = recompute_sector_validation(item)
            if sector_check["errors"]:
                failures.extend(f"market universe sector {sector}: {error}" for error in sector_check["errors"])
            if item.get("status") != "cross_source_verified":
                failures.append(f"market universe sector {sector}: status is not cross_source_verified")
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
        for asset in ("BTC", "ETH"):
            dat_item = market_universe.get("dat", {}).get(asset, {})
            dat_recomputed = recompute_dat_validation(dat_item)
            for error in dat_recomputed["errors"]:
                failures.append(f"{asset} DAT reconstruction: {error}")
            validation = dat_item.get("validation", {})
            for key in (
                "provider_count",
                "matched_company_count",
                "matched_base_holdings",
                "representative_coverage_ratio",
                "weighted_cross_source_gap",
                "maximum_company_gap",
                "excluded_outlier_count",
            ):
                assert_close(f"{asset} DAT {key}", dat_recomputed.get(key), validation.get(key), failures)
            if sorted(validation.get("providers") or []) != dat_recomputed["providers"]:
                failures.append(f"{asset} DAT participating provider list mismatch")
            if dat_item.get("status") != dat_recomputed["status"] or validation.get("status") != dat_recomputed["status"]:
                failures.append(f"{asset} DAT claimed status does not match independent reconstruction")
            if int(dat_item.get("source_count") or 0) != dat_recomputed["provider_count"]:
                failures.append(f"{asset} DAT source_count does not match reconstructed participating providers")
            official_specs = {
                "BTC": {"MSTR": ["mstr_sec_btc_holdings_latest"]},
                "ETH": {"BMNR": ["bmnr_eth_holdings"], "SBET": ["sbet_eth_holdings_equivalent"]},
            }[asset]
            comparisons_by_symbol = {
                str(comparison.get("symbol")): comparison
                for comparison in validation.get("comparisons", [])
                if comparison.get("symbol")
            }
            canonical_companies = {
                str(company.get("symbol")): company
                for company in dat_item.get("companies", [])
                if company.get("symbol")
            }
            official_values: dict[str, float] = {}
            for symbol, observation_names in official_specs.items():
                official_observation = next(
                    (observations.get(name) for name in observation_names if observations.get(name, {}).get("ok")),
                    None,
                )
                official_value = as_float(official_observation.get("value")) if official_observation else None
                if official_value is None:
                    failures.append(f"{asset} DAT {symbol}: required raw official observation missing")
                    continue
                official_values[symbol] = official_value
                comparison_value = as_float(
                    comparisons_by_symbol.get(symbol, {}).get("provider_values", {}).get("SEC official filings")
                )
                assert_close(f"{asset} DAT {symbol} raw official binding", official_value, comparison_value, failures)
                assert_close(
                    f"{asset} DAT {symbol} official overlay binding",
                    official_value,
                    canonical_companies.get(symbol, {}).get("holdings"),
                    failures,
                )
            overlay_recomputed = recompute_dat_official_overlay(dat_item, official_values)
            for error in overlay_recomputed["errors"]:
                failures.append(f"{asset} DAT official overlay reconstruction: {error}")
            assert_close(
                f"{asset} DAT official overlay adjustment",
                overlay_recomputed["official_overlay_adjustment"],
                dat_item.get("official_overlay_adjustment"),
                failures,
            )
            assert_close(
                f"{asset} DAT official-overlay total",
                overlay_recomputed["expected_total_holdings"],
                dat_item.get("total_holdings"),
                failures,
            )
            evidence.append(
                f"{asset} DAT reconstructed status={dat_recomputed['status']} providers={dat_recomputed['provider_count']} "
                f"coverage={dat_recomputed['representative_coverage_ratio']} gap={dat_recomputed['weighted_cross_source_gap']}"
            )
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
        stablecoin_prior = as_float(credit.get("stablecoin_supply_30d_ago_usd"))
        stablecoin_asset_sum = as_float(credit.get("stablecoin_supply_asset_sum_usd"))
        matched_supply = as_float(credit.get("stablecoin_supply_matched_cohort_usd"))
        matched_prior = as_float(credit.get("stablecoin_supply_matched_cohort_30d_ago_usd"))
        if stablecoin_supply is not None and stablecoin_prior not in (None, 0):
            assert_close("BTC thesis timestamped stablecoin 30d change", stablecoin_supply / stablecoin_prior - 1, credit.get("stablecoin_supply_30d_change"), structural_failures)
        else:
            structural_failures.append("BTC thesis timestamped stablecoin current/prior values missing")
        if stablecoin_supply is not None and stablecoin_asset_sum is not None:
            assert_close("BTC thesis stablecoin asset-sum gap", pct_gap(stablecoin_supply, stablecoin_asset_sum), credit.get("stablecoin_supply_asset_sum_gap"), structural_failures)
        if matched_supply is None or matched_prior in (None, 0):
            structural_failures.append("BTC thesis stablecoin matched-cohort evidence missing")
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
        btcfi = thesis.get("unmeasured_falsifier", {})
        btcfi_tvl = as_float(btcfi.get("observable_btcfi_tvl_usd"))
        btcfi_count = int(btcfi.get("observable_protocol_count") or 0)
        btcfi_categories = btcfi.get("included_categories", [])
        if (
            btcfi_tvl is None
            or btcfi_tvl <= 0
            or btcfi_count < 1
            or sorted(btcfi_categories) != ["Anchor BTC", "Decentralized BTC", "Restaked BTC"]
            or btcfi.get("status") != "measured_onchain_proxy_global_total_unknown"
        ):
            structural_failures.append("BTC thesis BTCFi observable collateral proxy contract is incomplete")
        credit_max_lag = as_float(
            market_universe.get("quality", {})
            .get("freshness_contract", {})
            .get("thesis_credit_max_lag_hours")
        )
        credit_age = age_hours(credit.get("as_of"))
        if credit_max_lag is None or credit_max_lag < 24:
            structural_failures.append("BTC thesis stablecoin/RWA freshness contract missing or incompatible with daily source cadence")
        elif credit_age is None or credit_age < -0.25 or credit_age > credit_max_lag:
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
        collateral = thesis.get("unmeasured_falsifier", {})
        if (
            collateral.get("status") != "measured_onchain_proxy_global_total_unknown"
            or collateral.get("global_total_status") != "unknown_no_complete_public_dataset"
        ):
            structural_failures.append("BTC thesis must distinguish measured onchain BTCFi proxy from unknown global collateral stock")
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

    prices = snapshot.get("metrics", {}).get("prices", {})
    price_basis = snapshot.get("metrics", {}).get("price_basis", {})
    check_spot_source_pool(
        "BTC spot",
        ["btc_usd_coingecko", "btc_usd_coinbase", "btc_usd_kraken"],
        observations,
        prices.get("btc_usd"),
        price_basis.get("btc_usd", {}),
        failures,
        warnings,
        evidence,
    )
    check_spot_source_pool(
        "ETH spot",
        ["eth_usd_coingecko", "eth_usd_coinbase", "eth_usd_kraken"],
        observations,
        prices.get("eth_usd"),
        price_basis.get("eth_usd", {}),
        failures,
        warnings,
        evidence,
    )
    if as_float(prices.get("mstr_usd")) is None:
        failures.append("MSTR price: Yahoo 與 Nasdaq 備援池皆不可用")
    check_observation_freshness("BTC MVRV", observations.get("btc_mvrv_current"), 3, 7, failures, degradations, evidence)
    strategy_holdings = observations.get("mstr_sec_btc_holdings_latest") or observations.get("mstr_strategy_btc_holdings")
    check_observation_freshness("Strategy BTC holdings", strategy_holdings, 14, 45, failures, degradations, evidence)
    sec_sales = observations.get("mstr_sec_rolling_7d_sales_musd")
    if sec_sales and sec_sales.get("ok"):
        check_observation_freshness("Strategy latest complete disclosed week sales", sec_sales, 8, 14, failures, degradations, evidence)
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
    for asset, prefix in (("BTC", "etf_flow"), ("ETH", "eth_etf_flow")):
        etf_status = str(radar.get(f"{prefix}_status") or "")
        if etf_status != "sample_cross_source_verified":
            degradations.append(f"{asset} ETF flow sample verification unavailable; not eligible as hard trigger")
            continue
        etf_source_count = as_float(radar.get(f"{prefix}_source_count"))
        component_completeness = as_float(radar.get(f"{prefix}_component_completeness"))
        etf_official_gap = as_float(radar.get(f"{prefix}_official_major_fund_gap"))
        etf_official_coverage = as_float(radar.get(f"{prefix}_official_major_fund_coverage"))
        backup_gap = as_float(radar.get(f"{prefix}_backup_component_gap"))
        backup_coverage = as_float(radar.get(f"{prefix}_backup_component_coverage"))
        etf_as_of_age = age_days(radar.get(f"{prefix}_as_of"))
        if etf_source_count is None or etf_source_count < 3:
            failures.append(f"{asset} ETF sample verification has insufficient validation sources: {etf_source_count}")
        if component_completeness is None or component_completeness < 0.95:
            failures.append(f"{asset} ETF latest fund roster completeness below 95%: {component_completeness}")
        if etf_official_gap is None or etf_official_gap > 0.05:
            failures.append(f"{asset} ETF official major-fund gap exceeds 5% or USD 5m: {etf_official_gap}")
        if etf_official_coverage is None or etf_official_coverage < 0.30:
            failures.append(f"{asset} ETF official major-fund gross component coverage below 30%: {etf_official_coverage}")
        if backup_gap is None or backup_gap > 0.05:
            failures.append(f"{asset} ETF same-date backup sample gap exceeds 5% or USD 5m: {backup_gap}")
        if backup_coverage is None or backup_coverage < 0.30:
            failures.append(f"{asset} ETF same-date backup sample gross coverage below 30%: {backup_coverage}")
        if etf_as_of_age is None or etf_as_of_age < 0 or etf_as_of_age > 5:
            failures.append(f"{asset} ETF market date stale or missing: age_days={etf_as_of_age}")
        if any(as_float(radar.get(f"{prefix}_{window}_usd")) is None for window in ("1d", "7d", "30d")):
            failures.append(f"{asset} ETF rolling windows missing despite sample-verified status")
        try:
            validation_inputs = json.loads(str(radar.get(f"{prefix}_validation_inputs_json") or ""))
        except json.JSONDecodeError:
            validation_inputs = {}
            failures.append(f"{asset} ETF offline-reconstructable validation inputs missing")
        etf_recomputed = recompute_etf_validation(validation_inputs, asset)
        for error in etf_recomputed["errors"]:
            failures.append(f"{asset} ETF reconstruction: {error}")
        if validation_inputs.get("canonical_provider") not in {"The Block", "Blockworks / Trackinsights", "Bitbo"}:
            failures.append(f"{asset} ETF canonical provider is not an approved fund-component source")
        if validation_inputs.get("canonical_as_of") != radar.get(f"{prefix}_as_of"):
            failures.append(f"{asset} ETF canonical date does not match published as_of")
        canonical_age = age_hours(validation_inputs.get("canonical_updated_at"))
        if canonical_age is None or canonical_age < -1 or canonical_age > 36:
            failures.append(f"{asset} ETF canonical source update timestamp is missing or stale")
        assert_close(f"{asset} ETF recorded component sum", etf_recomputed["canonical_component_sum_usd"], validation_inputs.get("canonical_component_sum_usd"), failures, tolerance=1e-9)
        assert_close(f"{asset} ETF component/total difference", etf_recomputed["canonical_total_difference_usd"], validation_inputs.get("canonical_total_difference_usd"), failures)
        assert_close(f"{asset} ETF component/total tolerance", etf_recomputed["canonical_total_tolerance_usd"], validation_inputs.get("canonical_total_tolerance_usd"), failures)
        if not etf_recomputed["canonical_total_reconciled"] or validation_inputs.get("canonical_total_reconciled") is not True:
            failures.append(f"{asset} ETF fund components do not reconcile to the reported total within USD 500k or 0.1%")
        assert_close(f"{asset} ETF gross component flow", etf_recomputed["gross_component_flow_usd"], validation_inputs.get("gross_component_flow_usd"), failures, tolerance=1e-9)
        assert_close(f"{asset} ETF component count", etf_recomputed["component_count"], validation_inputs.get("component_count"), failures)
        assert_close(f"{asset} ETF component completeness", etf_recomputed["component_completeness"], component_completeness, failures)
        assert_close(f"{asset} ETF official normalized gap", etf_recomputed["official_gap"], etf_official_gap, failures)
        assert_close(f"{asset} ETF official gross coverage", etf_recomputed["official_coverage"], etf_official_coverage, failures)
        assert_close(f"{asset} ETF backup max component gap", etf_recomputed["backup_max_gap"], backup_gap, failures)
        assert_close(f"{asset} ETF backup gross coverage", etf_recomputed["backup_coverage"], backup_coverage, failures)
        assert_close(f"{asset} ETF validation source count", etf_recomputed["validation_source_count"], etf_source_count, failures)
        backup_sample = validation_inputs.get("backup_sample", {})
        assert_close(f"{asset} ETF backup weighted gap", etf_recomputed["backup_weighted_gap"], backup_sample.get("normalized_gap"), failures)
        assert_close(f"{asset} ETF backup recorded max gap", etf_recomputed["backup_max_gap"], backup_sample.get("maximum_component_gap"), failures)
        if not etf_recomputed["backup_same_date"]:
            failures.append(f"{asset} ETF backup sample is not from the canonical market date")
        if int(as_float(validation_inputs.get("validation_source_count")) or 0) != int(etf_recomputed["validation_source_count"]):
            failures.append(f"{asset} ETF recorded validation source count mismatch")
        if bool(validation_inputs.get("amount_sanity_pass")) != etf_recomputed["amount_sanity_pass"]:
            failures.append(f"{asset} ETF amount-sanity claim mismatch")
        sanity_thresholds = validation_inputs.get("amount_sanity_thresholds", {})
        if as_float(sanity_thresholds.get("maximum_absolute_single_fund_daily_flow_usd")) != ETF_MAX_ABS_DAILY_FUND_FLOW_USD:
            failures.append(f"{asset} ETF single-fund sanity threshold mismatch")
        if as_float(sanity_thresholds.get("maximum_gross_daily_flow_usd")) != ETF_MAX_GROSS_DAILY_FLOW_USD:
            failures.append(f"{asset} ETF gross-flow sanity threshold mismatch")
        independently_verified = bool(
            etf_recomputed["component_completeness"] is not None
            and etf_recomputed["component_completeness"] >= 0.95
            and etf_recomputed["official_gap"] is not None
            and etf_recomputed["official_gap"] <= 0.05
            and etf_recomputed["official_coverage"] is not None
            and etf_recomputed["official_coverage"] >= 0.30
            and etf_recomputed["backup_max_gap"] is not None
            and etf_recomputed["backup_max_gap"] <= 0.05
            and etf_recomputed["backup_coverage"] is not None
            and etf_recomputed["backup_coverage"] >= 0.30
            and etf_recomputed["validation_source_count"] >= 3
            and etf_recomputed["canonical_total_reconciled"]
            and etf_recomputed["amount_sanity_pass"]
            and not etf_recomputed["errors"]
        )
        if not independently_verified:
            failures.append(f"{asset} ETF sample-verified claim failed independent reconstruction")
        market_etf = market_universe.get("etf", {}).get(asset, {})
        if market_etf.get("validation_inputs_json") != radar.get(f"{prefix}_validation_inputs_json"):
            failures.append(f"{asset} ETF market-universe validation evidence is not bound to the daily snapshot")
        evidence.append(
            f"{asset} ETF sample verified validation_sources={etf_source_count} completeness={component_completeness} "
            f"official_gap={etf_official_gap} official_coverage={etf_official_coverage} "
            f"backup_gap={backup_gap} backup_coverage={backup_coverage}"
        )
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
            failures.append("BTC standard: ETF flow weight must remain capped at 0.5 even after cross-source verification")
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
    manual_risk_keys = ["mstr_btc_holdings", "usd_reserve_musd", "cash_other_musd", "debt_face_musd", "annual_interest_musd", "other_debt_annual_service_musd", "preferred", "preferred_aggregate_musd", "weekly_btc_sales_musd", "common_shares_outstanding_m", "deferred_tax_liability_musd", "prev_pref_notional_musd", "prev_mnav_equity"]
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
            warn_after, fail_after = 8, 14
        if field_name == "preferred":
            warn_after, fail_after = 10, 21
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
    preferred_class_total = sum(as_float(item.get("notional_musd")) or 0 for item in manual.get("preferred", {}).values())
    preferred_aggregate = as_float(manual.get("preferred_aggregate_musd"))
    if preferred_aggregate is None or preferred_aggregate < preferred_class_total:
        failures.append("preferred aggregate must be present and no lower than the class reconstruction")
    elif pct_gap(preferred_aggregate, preferred_class_total) > 0.03:
        failures.append("preferred aggregate and class reconstruction differ by more than 3%")

    failures = unique(failures)
    degradations = unique(degradations)
    warnings = unique(warnings)
    evidence = unique(evidence)
    structural_failures = unique(structural_failures)
    structural_degradations = unique(structural_degradations)
    structural_evidence = unique(structural_evidence)
    status = classify_verification_status(failures, degradations)
    structural_status = "fail" if structural_failures else ("degraded" if structural_degradations else "pass")
    report = {
        "schema": 2,
        "agent": "daily-data-verifier",
        "verified_at": now_iso(),
        "date": snapshot.get("date"),
        "batch_id": snapshot.get("batch_id"),
        "raw_generated_at": raw.get("generated_at"),
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
            "verification_scope": "independent formula, lineage, ETF evidence and DAT consensus reconstruction; upstream HTTP responses are not all re-fetched",
        },
        "policy": {
            "btc_cross_source_max_gap": "1.5%",
            "eth_cross_source_max_gap": "1.5%",
            "equity_cross_source_max_gap_same_basis": "2%",
            "equity_mismatched_quote_basis": "1-2% is warning only; above 2% is degraded, never a same-basis hard fail",
            "daily_equity_snapshot_basis": "Yahoo regular-market close preferred; Nasdaq quote is backup/freshness evidence",
            "required_sources": ["BTC/ETH 現貨來源池至少 2 個：CoinGecko、Coinbase、Kraken", "MSTR：Yahoo 優先、Nasdaq 備援"],
            "degraded_if_missing": ["Nasdaq backup quotes", "SEC EDGAR submissions", "automated capital-structure inputs", "cross-source ETF flow verification", "BTC MVRV ratio"],
            "btc_standard_required_inputs": ["BTC spot cross-source", "BTC 50/200DMA", "BTC MVRV ratio", "Fear & Greed", "ETF flow context"],
            "freshness_limits": {"BTC MVRV": "warn >3d, fail >7d", "Strategy holdings": "warn >14d, fail >45d", "Strategy latest complete disclosed week sales": "warn >8d, fail >14d", "USD reserve": "warn >30d, fail >120d", "MSTR common shares": "warn >45d, fail >120d", "BMNR holdings": "warn >14d, fail >30d"},
            "not_hard_triggers": ["ETF flow as a standalone dimension even when cross-source verified", "realized loss without stable free API", "Google Trends without official unauthenticated API", "macro calendar without official free event API"],
            "market_universe": {
                "update_target": "hourly",
                "fail_if_stale": ">3h",
                "tracked_assets": ["BTC", "ETH", "HYPE", "SOL", "BNB", "XRP", "DOGE"],
                "derivatives": ["Bybit/OKX/Hyperliquid and available Binance perpetuals", "Deribit near-90-day dated futures with OKX fallback", "CME Yahoo proxy", "Deribit DVOL/options with labeled OKX ATM-IV/options fallback"],
                "coverage_rule": "provider failures are incidents, not quality downgrades, when the field still passes source-count, freshness and divergence checks; unknown data never becomes zero",
            },
        },
    }
    write_json(REPORT_PATH, report)
    print(json.dumps({"status": status, "failures": len(failures), "degradations": len(degradations), "warnings": len(warnings)}, ensure_ascii=False))
    return 0 if status != "fail" else 1


if __name__ == "__main__":
    sys.exit(main())
