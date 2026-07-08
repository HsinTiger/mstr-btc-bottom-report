#!/usr/bin/env python3
"""Institutional analytics layer for the personal Bloomberg dashboard.

This layer converts verified daily data into professional-grade analysis:
quality, attribution, scenario sensitivity, and a risk stack.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "daily"
SNAPSHOT_PATH = DATA_DIR / "latest_snapshot.json"
DATABASE_PATH = DATA_DIR / "database.json"
VERIFY_PATH = DATA_DIR / "agent_verification_report.json"
ANALYTICS_PATH = DATA_DIR / "institutional_analytics.json"
LOGIC_AUDIT_PATH = DATA_DIR / "logic_audit.json"


def load_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def f(value: Any) -> float | None:
    try:
        if value is None:
            return None
        value = float(value)
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    except (TypeError, ValueError):
        return None


def pct_change(now: float | None, prev: float | None) -> float | None:
    if now is None or prev in (None, 0):
        return None
    return now / prev - 1


def delta(now: float | None, prev: float | None) -> float | None:
    if now is None or prev is None:
        return None
    return now - prev


def fmt(value: Any, digits: int = 2) -> str:
    value = f(value)
    return "N/A" if value is None else f"{value:.{digits}f}"


def pct(value: Any) -> str:
    value = f(value)
    return "N/A" if value is None else f"{value * 100:.1f}%"


def latest_previous(database: dict[str, Any], date: str) -> dict[str, Any] | None:
    snapshots = [item for item in database.get("snapshots", []) if item.get("date") != date]
    snapshots.sort(key=lambda item: item.get("date", ""))
    return snapshots[-1] if snapshots else None


def confidence_from_verification(verification: dict[str, Any], provenance: dict[str, Any]) -> str:
    if verification.get("status") == "fail":
        return "low"
    if verification.get("status") == "degraded" or provenance.get("status") != "automated":
        return "medium-low"
    return "high"


def mstr_decomposition(snapshot: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    prices = snapshot["metrics"]["prices"]
    metrics = snapshot["metrics"]["mstr_metrics"]
    prev_prices = previous.get("metrics", {}).get("prices", {}) if previous else {}
    prev_metrics = previous.get("metrics", {}).get("mstr_metrics", {}) if previous else {}
    btc_ret = pct_change(f(prices.get("btc_usd")), f(prev_prices.get("btc_usd")))
    mstr_ret = pct_change(f(prices.get("mstr_usd")), f(prev_prices.get("mstr_usd")))
    m1_delta = delta(f(metrics.get("equity_mnav")), f(prev_metrics.get("equity_mnav")))
    strc_delta = delta(f(metrics.get("strc_discount")), f(prev_metrics.get("strc_discount")))
    beta_gap = None if btc_ret is None or mstr_ret is None else mstr_ret - btc_ret
    drivers = []
    if beta_gap is not None:
        drivers.append({"driver": "MSTR_vs_BTC", "value": beta_gap, "read": "MSTR underperformed BTC; discount/capital-structure pressure persists" if beta_gap < 0 else "MSTR outperformed BTC; verify it is not short-term reflexivity"})
    if m1_delta is not None:
        drivers.append({"driver": "M1_delta", "value": m1_delta, "read": "Common-equity safety margin deteriorated" if m1_delta < 0 else "Common-equity safety margin improved"})
    if strc_delta is not None:
        drivers.append({"driver": "STRC_discount_delta", "value": strc_delta, "read": "Preferred-stock trust signal deteriorated" if strc_delta > 0 else "Preferred-stock trust signal improved"})
    return {
        "btc_return_1d": btc_ret,
        "mstr_return_1d": mstr_ret,
        "mstr_minus_btc_1d": beta_gap,
        "m1_delta_1d": m1_delta,
        "strc_discount_delta_1d": strc_delta,
        "drivers": drivers,
    }


def sensitivity(snapshot: dict[str, Any]) -> dict[str, Any]:
    prices = snapshot["metrics"]["prices"]
    inputs = snapshot["metrics"]["manual_inputs"]
    btc = f(prices.get("btc_usd"))
    mstr = f(prices.get("mstr_usd"))
    if btc is None or mstr is None:
        return {"scenarios": []}
    pref_total = sum((f(item.get("notional_musd")) or 0) for item in inputs.get("preferred", {}).values())
    shares = f(inputs.get("diluted_shares_m")) or 0
    btc_holdings = f(inputs.get("mstr_btc_holdings")) or 0
    debt = f(inputs.get("debt_face_musd")) or 0
    cash = (f(inputs.get("usd_reserve_musd")) or 0) + (f(inputs.get("cash_other_musd")) or 0)
    dtl = f(inputs.get("net_deferred_tax_liability_musd")) or 0
    scenarios = []
    for shock in [-0.2, -0.1, 0, 0.1, 0.2]:
        btc_s = btc * (1 + shock)
        btc_nav = btc_holdings * btc_s / 1e6
        net_to_common = btc_nav + cash - debt - pref_total - dtl
        mkt_cap = shares * mstr
        m1 = mkt_cap / net_to_common if net_to_common > 0 else None
        scenarios.append({"btc_shock": shock, "btc_price": btc_s, "equity_mnav_at_current_mstr": m1})
    return {"scenarios": scenarios}


def risk_stack(snapshot: dict[str, Any], verification: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = snapshot["metrics"]["mstr_metrics"]
    radar = snapshot["metrics"].get("market_radar", {})
    stack = []

    def add(name: str, severity: str, value: str, read: str) -> None:
        stack.append({"name": name, "severity": severity, "value": value, "read": read})

    add("Automation quality", "medium" if verification.get("status") == "degraded" else verification.get("status", "unknown"), verification.get("status", "unknown"), "Research-grade only" if verification.get("status") == "degraded" else "Data quality requires review")
    add("M1 common-equity margin", "high" if (f(metrics.get("equity_mnav")) or 0) < 1 else "low", fmt(metrics.get("equity_mnav")) + "x", "Below 1.0; common equity is not cheap enough" if (f(metrics.get("equity_mnav")) or 0) < 1 else "Above 1.0; still check red flags")
    add("M5 BTC-sale pressure", "high" if (f(metrics.get("sale_ratio")) or 0) > 2 else "low", fmt(metrics.get("sale_ratio"), 1) + "x", "Above 2x; freeze tactical adds" if (f(metrics.get("sale_ratio")) or 0) > 2 else "Below red-line threshold")
    add("M7 STRC trust vote", "high" if (f(metrics.get("strc_discount")) or 0) > 0.05 else "low", pct(metrics.get("strc_discount")), "Discount too deep; downgrade all mNAV signals" if (f(metrics.get("strc_discount")) or 0) > 0.05 else "Trust vote improving")
    add("Sentiment", "opportunity" if (f(radar.get("fear_greed")) or 50) <= 25 else "neutral", fmt(radar.get("fear_greed"), 0), "Fear helps setup quality; not a buy trigger by itself" if (f(radar.get("fear_greed")) or 50) <= 25 else "Not capitulation sentiment")
    return stack


def main() -> int:
    snapshot = load_json(SNAPSHOT_PATH)
    database = load_json(DATABASE_PATH)
    verification = load_json(VERIFY_PATH)
    logic_audit = load_json(LOGIC_AUDIT_PATH, {})
    previous = latest_previous(database, snapshot["date"])
    provenance = snapshot.get("metrics", {}).get("manual_input_provenance", {})
    analytics = {
        "schema": 1,
        "date": snapshot["date"],
        "generated_at": now_iso(),
        "quality": {
            "verification_status": verification.get("status"),
            "confidence": confidence_from_verification(verification, provenance),
            "degradations": verification.get("degradations", []),
            "logic_audit_status": logic_audit.get("status", "not_run"),
            "logic_failed_invariants": logic_audit.get("summary", {}).get("failed_invariants"),
            "logic_contradictions": logic_audit.get("summary", {}).get("contradictions"),
        },
        "executive_read": {
            "headline": "Research-grade only; not auto-trading grade" if verification.get("status") == "degraded" else snapshot.get("decision", {}).get("state"),
            "one_line": "M1 is below 1.0 and M5/M7 red flags remain; today is about avoiding chase, not finding reasons to add.",
        },
        "logic_audit": {
            "status": logic_audit.get("status", "not_run"),
            "plain_english": logic_audit.get("decision", {}).get("plain_english", "Logic audit has not run yet."),
            "blocked_actions": logic_audit.get("decision", {}).get("blocked_actions", []),
            "failed_invariants": logic_audit.get("summary", {}).get("failed_invariants"),
            "contradictions": logic_audit.get("summary", {}).get("contradictions"),
        },
        "decomposition": mstr_decomposition(snapshot, previous),
        "sensitivity": sensitivity(snapshot),
        "risk_stack": risk_stack(snapshot, verification),
    }
    write_json(ANALYTICS_PATH, analytics)
    print(json.dumps({"analytics": str(ANALYTICS_PATH), "confidence": analytics["quality"]["confidence"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
