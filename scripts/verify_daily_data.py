#!/usr/bin/env python3
"""Independent data-verification agent for the daily MSTR/BTC dataset."""

from __future__ import annotations

import json
import math
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "daily"
RAW_PATH = DATA_DIR / "raw_observations.json"
SNAPSHOT_PATH = DATA_DIR / "latest_snapshot.json"
REPORT_PATH = DATA_DIR / "agent_verification_report.json"


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
    observations = obs_map(raw)
    failures: list[str] = []
    warnings: list[str] = []
    degradations: list[str] = []
    evidence: list[str] = []

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
    check_observation_freshness("Strategy BTC holdings", observations.get("mstr_strategy_btc_holdings"), 14, 45, failures, degradations, evidence)
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
    if etf_status == "automated":
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
    status = "fail" if failures else ("degraded" if degradations or warnings else "pass")
    report = {
        "schema": 1,
        "agent": "daily-data-verifier",
        "verified_at": now_iso(),
        "date": snapshot.get("date"),
        "status": status,
        "failures": failures,
        "degradations": degradations,
        "warnings": warnings,
        "evidence": evidence,
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
        },
    }
    write_json(REPORT_PATH, report)
    print(json.dumps({"status": status, "failures": len(failures), "degradations": len(degradations), "warnings": len(warnings)}, ensure_ascii=False))
    return 0 if status != "fail" else 1


if __name__ == "__main__":
    sys.exit(main())
