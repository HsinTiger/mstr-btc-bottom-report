#!/usr/bin/env python3
"""Decision-logic audit for the personal Bloomberg dashboard.

This layer checks whether the daily data, verifier, and analytics are logically
consistent. Its job is to prevent false green lights for leveraged MSTR trades.
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
VERIFY_PATH = DATA_DIR / "agent_verification_report.json"
ANALYTICS_PATH = DATA_DIR / "institutional_analytics.json"
AUDIT_PATH = DATA_DIR / "logic_audit.json"


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


def n(value: Any) -> float | None:
    try:
        if value is None:
            return None
        value = float(value)
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    except (TypeError, ValueError):
        return None


def pct(value: Any) -> str:
    value = n(value)
    return "N/A" if value is None else f"{value * 100:.1f}%"


def num(value: Any, digits: int = 2) -> str:
    value = n(value)
    return "N/A" if value is None else f"{value:.{digits}f}"


def add_invariant(items: list[dict[str, Any]], *, rule_id: str, passed: bool, evidence: str, risk_if_failed: str, severity: str = "blocking") -> None:
    items.append({
        "id": rule_id,
        "status": "pass" if passed else "fail",
        "severity": severity,
        "evidence": evidence,
        "risk_if_failed": risk_if_failed,
    })


def build_thresholds(metrics: dict[str, Any], radar: dict[str, Any]) -> list[dict[str, Any]]:
    equity_mnav = n(metrics.get("equity_mnav"))
    enterprise_mnav = n(metrics.get("enterprise_mnav"))
    sale_ratio = n(metrics.get("sale_ratio"))
    strc_discount = n(metrics.get("strc_discount"))
    coverage_months = n(metrics.get("coverage_months"))
    fear_greed = n(radar.get("fear_greed"))
    treasury = n(radar.get("treasury_avg_bill_rate_pct"))
    return [
        {
            "metric": "M1 equity_mnav",
            "threshold": "< 1.00 blocks common-equity cheapness claims",
            "current": f"{num(equity_mnav, 3)}x",
            "rationale": "Below 1.0 means common equity trades through internally adjusted BTC NAV after debt, preferred, cash, and tax inputs.",
            "action": "Do not frame MSTR common as cheap until provenance and capital stack are reviewed.",
        },
        {
            "metric": "M2 enterprise_mnav",
            "threshold": "< 1.00 would imply enterprise trades below BTC NAV before common-stack haircuts",
            "current": f"{num(enterprise_mnav, 3)}x",
            "rationale": "Separates company-level BTC premium from common-share capital-structure burden.",
            "action": "Use only as context; never override M1/M5/M7 red flags.",
        },
        {
            "metric": "M5 weekly BTC-sale pressure",
            "threshold": "> 2.00 is forced-sale pressure red flag",
            "current": f"{num(sale_ratio, 2)}x",
            "rationale": "Weekly BTC sales versus economic cushion can turn financing reflexivity from tailwind to headwind.",
            "action": "Block 2.5x tactical adds when above threshold.",
        },
        {
            "metric": "M7 STRC discount",
            "threshold": "> 5.0% downgrades all mNAV signals",
            "current": pct(strc_discount),
            "rationale": "Preferred-stock market is the capital-structure trust vote; a wide discount can invalidate headline mNAV optimism.",
            "action": "Treat mNAV as suspect until preferred market stabilizes.",
        },
        {
            "metric": "USD reserve coverage",
            "threshold": "< 12 months weakens survivability buffer",
            "current": f"{num(coverage_months, 1)} months",
            "rationale": "Cash duration matters more than narrative when dividends, interest, or ATM access tighten.",
            "action": "Escalate to risk-off review if coverage drops below one year.",
        },
        {
            "metric": "Fear & Greed",
            "threshold": "< 25 is panic context, not automatic buy signal",
            "current": num(fear_greed, 0),
            "rationale": "Sentiment can mark opportunity only when structure and verification are clean.",
            "action": "Allow research watchlist; require M1/M5/M7 confirmation before action.",
        },
        {
            "metric": "Treasury bill rate proxy",
            "threshold": "> 4.5% tightens preferred/debt refinancing pressure",
            "current": f"{num(treasury, 2)}%",
            "rationale": "Risk-free yield competes with STRC and raises required return on capital-stack instruments.",
            "action": "Add financing-risk weight when rates rise.",
        },
    ]


def build_falsification_watch() -> list[dict[str, Any]]:
    return [
        {
            "hypothesis": "M5 > 2 means financing pressure blocks tactical 2.5x MSTR adds.",
            "what_would_disprove": "Repeated M5 > 2 while STRC discount narrows, reserve coverage improves, and MSTR outperforms BTC across multiple weeks.",
            "required_evidence": "At least 4 weekly observations, preferred-market confirmation, and no verifier degradation.",
        },
        {
            "hypothesis": "STRC discount > 5% is a capital-structure trust warning.",
            "what_would_disprove": "Discount remains wide for liquidity-only reasons while preferred coverage, issuance terms, and MSTR/BTC relative strength improve.",
            "required_evidence": "Bid/ask or volume context plus independent preferred-price source.",
        },
        {
            "hypothesis": "M1 below 1.0 means common-equity cheapness claims require extra review.",
            "what_would_disprove": "Reviewed capital inputs show diluted shares, debt, preferred liquidation value, and deferred tax liabilities were overstated.",
            "required_evidence": "SEC filing extraction promoted from manual_seed to reviewed input provenance.",
        },
        {
            "hypothesis": "Degraded verifier status means research-grade only.",
            "what_would_disprove": "All required sources cross-check within tolerance and manual capital-structure inputs are reviewed or automated.",
            "required_evidence": "agent_verification_report.status == pass plus reviewed provenance.",
        },
        {
            "hypothesis": "ETF-flow signals are not decision-grade until automated.",
            "what_would_disprove": "Daily ETF flow feed is sourced, cross-checked, timestamped, and included in verifier evidence.",
            "required_evidence": "Non-stale ETF source with independent backup and explicit tolerance policy.",
        },
    ]


def main() -> None:
    snapshot = load_json(SNAPSHOT_PATH)
    verification = load_json(VERIFY_PATH, {})
    analytics = load_json(ANALYTICS_PATH, {})

    metrics = snapshot.get("metrics", {}).get("mstr_metrics", {})
    radar = snapshot.get("metrics", {}).get("market_radar", {})
    provenance = snapshot.get("metrics", {}).get("manual_input_provenance", {})
    decision = snapshot.get("decision", {})
    quality = analytics.get("quality", {})
    executive = analytics.get("executive_read", {})

    contract_red_light = metrics.get("contract_red_light") is True
    equity_mnav = n(metrics.get("equity_mnav"))
    enterprise_mnav = n(metrics.get("enterprise_mnav"))
    sale_ratio = n(metrics.get("sale_ratio"))
    strc_discount = n(metrics.get("strc_discount"))
    coverage_months = n(metrics.get("coverage_months"))
    sats_per_share = n(metrics.get("sats_per_share"))
    pref_dilution_flag = metrics.get("pref_dilution_flag") is True
    mnav_gate_ok = metrics.get("mnav_gate_ok") is True
    verification_status = verification.get("status", "missing")
    analytics_confidence = quality.get("confidence", "missing")
    provenance_status = provenance.get("status", "missing")
    etf_flow_status = radar.get("etf_flow_status", "missing")
    state = str(decision.get("state", ""))
    headline = str(executive.get("headline", ""))
    one_line = str(executive.get("one_line", ""))

    blocked_actions = set()
    allowed_actions = {"research", "observe"}
    if contract_red_light or (sale_ratio is not None and sale_ratio > 2):
        blocked_actions.add("add_2_5x_mstr")
    if strc_discount is None or strc_discount > 0.05:
        blocked_actions.add("treat_mnav_as_green")
    if equity_mnav is None or equity_mnav < 1:
        blocked_actions.add("claim_common_equity_cheap")
    if not mnav_gate_ok:
        blocked_actions.add("mnav_green_light")
    if verification_status != "pass" or provenance_status != "automated":
        blocked_actions.add("auto_trade")
    if etf_flow_status != "automated":
        blocked_actions.add("use_etf_flow_as_hard_trigger")

    invariants: list[dict[str, Any]] = []
    add_invariant(
        invariants,
        rule_id="NO_TACTICAL_ADD_WHEN_CONTRACT_RED_LIGHT",
        passed=(not contract_red_light) or "add_2_5x_mstr" in blocked_actions,
        evidence=f"contract_red_light={contract_red_light}, decision_state={state}, blocked_actions={sorted(blocked_actions)}",
        risk_if_failed="False green light for the user's 2.5x leveraged MSTR tactical sleeve.",
    )
    add_invariant(
        invariants,
        rule_id="DEGRADED_DATA_CANNOT_BE_AUTO_TRADING_GRADE",
        passed=(verification_status == "pass") or ("auto_trade" in blocked_actions and "Research-grade" in headline),
        evidence=f"verification={verification_status}, headline={headline}, confidence={analytics_confidence}",
        risk_if_failed="Research-grade data could be mistaken for execution-grade signal.",
    )
    add_invariant(
        invariants,
        rule_id="M1_BELOW_ONE_BLOCKS_CHEAPNESS_CLAIM",
        passed=(equity_mnav is None or equity_mnav >= 1) or "claim_common_equity_cheap" in blocked_actions,
        evidence=f"equity_mnav={num(equity_mnav, 3)}x, blocked_actions={sorted(blocked_actions)}",
        risk_if_failed="Common equity may be called cheap while adjusted common NAV is still below threshold.",
    )
    add_invariant(
        invariants,
        rule_id="MNAV_DUAL_GATE_REQUIRED",
        passed=mnav_gate_ok == ((equity_mnav is not None and equity_mnav >= 1) and (enterprise_mnav is not None and enterprise_mnav >= 1) and not pref_dilution_flag),
        evidence=f"mnav_gate_ok={mnav_gate_ok}, equity_mnav={num(equity_mnav, 3)}x, enterprise_mnav={num(enterprise_mnav, 3)}x, pref_dilution_flag={pref_dilution_flag}",
        risk_if_failed="Official or single-line mNAV could override the self-calculated dual gate.",
    )
    add_invariant(
        invariants,
        rule_id="COMMON_EQUITY_NAV_POSITIVE_OR_BLOCKED",
        passed=(equity_mnav is not None and equity_mnav > 0) or "claim_common_equity_cheap" in blocked_actions,
        evidence=f"equity_mnav={num(equity_mnav, 3)}x, sats_per_share={num(sats_per_share, 0)}",
        risk_if_failed="MSTR common could be treated as investable while net value to common is non-positive or uncomputable.",
    )
    add_invariant(
        invariants,
        rule_id="STRC_DISCOUNT_DOWNGRADES_MNAV",
        passed=(strc_discount is not None and strc_discount <= 0.05) or "treat_mnav_as_green" in blocked_actions,
        evidence=f"strc_discount={pct(strc_discount)}, blocked_actions={sorted(blocked_actions)}",
        risk_if_failed="Headline mNAV could ignore preferred-market capital-stack stress.",
    )
    add_invariant(
        invariants,
        rule_id="SALE_PRESSURE_BLOCKS_LEVERAGED_ADD",
        passed=(sale_ratio is not None and sale_ratio <= 2) or "add_2_5x_mstr" in blocked_actions,
        evidence=f"sale_ratio={num(sale_ratio, 2)}x, blocked_actions={sorted(blocked_actions)}",
        risk_if_failed="Forced-sale pressure could be treated as a dip-buy setup.",
    )
    add_invariant(
        invariants,
        rule_id="MANUAL_PROVENANCE_FORCES_DEGRADED_STATUS",
        passed=(provenance_status == "automated") or verification_status == "degraded",
        evidence=f"manual_input_provenance={provenance_status}, verification={verification_status}",
        risk_if_failed="Manual capital-stack seeds could be displayed as fully verified data.",
    )
    add_invariant(
        invariants,
        rule_id="ETF_FLOW_NOT_HARD_TRIGGER_UNTIL_AUTOMATED",
        passed=(etf_flow_status == "automated") or "use_etf_flow_as_hard_trigger" in blocked_actions,
        evidence=f"etf_flow_status={etf_flow_status}, blocked_actions={sorted(blocked_actions)}",
        risk_if_failed="Missing ETF-flow automation could still influence hard trading decisions.",
        severity="major",
    )
    add_invariant(
        invariants,
        rule_id="RESERVE_COVERAGE_ESCALATES_BELOW_ONE_YEAR",
        passed=(coverage_months is None or coverage_months >= 12) or "add_2_5x_mstr" in blocked_actions,
        evidence=f"coverage_months={num(coverage_months, 1)}, blocked_actions={sorted(blocked_actions)}",
        risk_if_failed="Liquidity runway deterioration could be missed in leveraged sizing.",
        severity="major",
    )

    contradictions: list[dict[str, Any]] = []
    if contract_red_light and any(word in state for word in ["加碼", "做多", "買進"]) and "禁止" not in state:
        contradictions.append({"id": "STATE_CONTRADICTS_CONTRACT_RED_LIGHT", "evidence": state})
    if verification_status != "pass" and any(word in headline.lower() for word in ["auto-trading grade", "green light", "buy"]):
        if "not auto-trading" not in headline.lower():
            contradictions.append({"id": "HEADLINE_OVERSTATES_DEGRADED_DATA", "evidence": headline})
    if equity_mnav is not None and equity_mnav < 1 and "cheap" in (headline + " " + one_line).lower():
        contradictions.append({"id": "CHEAPNESS_LANGUAGE_WITH_M1_BELOW_ONE", "evidence": headline + " / " + one_line})
    if mnav_gate_ok and (equity_mnav is None or equity_mnav < 1 or enterprise_mnav is None or enterprise_mnav < 1 or pref_dilution_flag):
        contradictions.append({"id": "MNAV_GATE_CONTRADICTS_COMPONENTS", "evidence": f"gate={mnav_gate_ok}, M1={num(equity_mnav, 3)}, M2={num(enterprise_mnav, 3)}, M3={pref_dilution_flag}"})
    if analytics.get("decomposition", {}).get("mstr_return_1d") and (equity_mnav is not None and equity_mnav < 1 or sale_ratio is not None and sale_ratio > 2 or strc_discount is not None and strc_discount > 0.05):
        mstr_return = n(analytics.get("decomposition", {}).get("mstr_return_1d"))
        if mstr_return is not None and mstr_return > 0 and "mnav_green_light" not in blocked_actions:
            contradictions.append({"id": "PRICE_REBOUND_WITHOUT_STRUCTURE_REPAIR", "evidence": f"mstr_return_1d={pct(mstr_return)}, M1={num(equity_mnav, 3)}, M5={num(sale_ratio, 2)}, M7={pct(strc_discount)}"})

    failed_blocking = [item for item in invariants if item["status"] == "fail" and item["severity"] == "blocking"]
    failed_any = [item for item in invariants if item["status"] == "fail"]
    if failed_blocking:
        status = "blocked"
    elif contradictions:
        status = "contradiction"
    elif failed_any:
        status = "review"
    else:
        status = "consistent"

    audit = {
        "schema": 1,
        "date": snapshot.get("date"),
        "generated_at": now_iso(),
        "status": status,
        "purpose": "Prevent false green lights by checking data quality, capital-structure stress, and decision-language consistency.",
        "summary": {
            "passed_invariants": len([item for item in invariants if item["status"] == "pass"]),
            "failed_invariants": len(failed_any),
            "contradictions": len(contradictions),
            "blocked_actions": sorted(blocked_actions),
            "allowed_actions": sorted(allowed_actions),
        },
        "decision": {
            "allowed_actions": sorted(allowed_actions),
            "blocked_actions": sorted(blocked_actions),
            "plain_english": "Only research/observe is allowed while verification is degraded or M5/M7 red flags remain.",
        },
        "invariants": invariants,
        "thresholds": build_thresholds(metrics, radar),
        "contradictions": contradictions,
        "falsification_watch": build_falsification_watch(),
    }
    write_json(AUDIT_PATH, audit)
    print(json.dumps({"logic_audit": str(AUDIT_PATH), "status": status, "failed_invariants": len(failed_any)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
