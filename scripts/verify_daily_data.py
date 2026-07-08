#!/usr/bin/env python3
"""Independent data-verification agent for the daily MSTR/BTC dataset."""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
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


def pct_gap(a: float, b: float) -> float:
    return abs(a - b) / ((abs(a) + abs(b)) / 2)


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
    sale_ratio = (as_float(inputs.get("weekly_btc_sales_musd")) or 0) / weekly_need if weekly_need else None
    sats_per_share = (as_float(inputs.get("mstr_btc_holdings")) or 0) * 1e8 / ((as_float(inputs.get("diluted_shares_m")) or 0) * 1e6) if as_float(inputs.get("diluted_shares_m")) else None
    equity_mnav = enterprise_mnav = None
    if btc_px and mstr_px:
        btc_nav_musd = (as_float(inputs.get("mstr_btc_holdings")) or 0) * btc_px / 1e6
        mkt_cap_musd = (as_float(inputs.get("diluted_shares_m")) or 0) * mstr_px
        net_to_common = btc_nav_musd + (as_float(inputs.get("usd_reserve_musd")) or 0) + (as_float(inputs.get("cash_other_musd")) or 0) - (as_float(inputs.get("debt_face_musd")) or 0) - pref_total - (as_float(inputs.get("net_deferred_tax_liability_musd")) or 0)
        equity_mnav = mkt_cap_musd / net_to_common if net_to_common > 0 else None
        enterprise_mnav = (mkt_cap_musd + (as_float(inputs.get("debt_face_musd")) or 0) + pref_total) / btc_nav_musd if btc_nav_musd else None
    pref_dilution_flag = bool(pref_total > (as_float(inputs.get("prev_pref_notional_musd")) or 0) and equity_mnav and equity_mnav > (as_float(inputs.get("prev_mnav_equity")) or 0))
    strc_discount = 1 - strc_px / 100 if strc_px else None
    return {
        "equity_mnav": equity_mnav,
        "enterprise_mnav": enterprise_mnav,
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

    for required in ["btc_usd_coingecko", "btc_usd_coinbase", "mstr_usd_yahoo"]:
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
    if radar.get("etf_flow_status") != "automated":
        degradations.append("ETF flow not automated")

    manual = snapshot.get("metrics", {}).get("manual_inputs", {})
    provenance = snapshot.get("metrics", {}).get("manual_input_provenance", {})
    fields = provenance.get("fields", {})
    manual_risk_keys = ["mstr_btc_holdings", "debt_face_musd", "weekly_btc_sales_musd", "diluted_shares_m", "net_deferred_tax_liability_musd"]
    manual_fields = [key for key in manual_risk_keys if fields.get(key, {}).get("source_type") == "manual" or key in manual]
    if manual_fields:
        degradations.append("manual capital-structure inputs: " + ", ".join(manual_fields))
    if provenance.get("status") != "automated":
        degradations.append(f"capital-structure provenance status={provenance.get('status', 'missing')}")

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
            "equity_cross_source_max_gap_same_basis": "2%",
            "equity_mismatched_quote_basis": "degraded, not fail",
            "daily_equity_snapshot_basis": "Yahoo regular-market close preferred; Nasdaq quote is backup/freshness evidence",
            "required_sources": ["CoinGecko", "Coinbase", "Yahoo Finance"],
            "degraded_if_missing": ["Nasdaq backup quotes", "SEC EDGAR submissions", "automated capital-structure inputs", "ETF flow automation"],
        },
    }
    write_json(REPORT_PATH, report)
    print(json.dumps({"status": status, "failures": len(failures), "degradations": len(degradations), "warnings": len(warnings)}, ensure_ascii=False))
    return 0 if status != "fail" else 1


if __name__ == "__main__":
    sys.exit(main())
