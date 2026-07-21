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
            "metric": "普通股市值／普通股淨值",
            "threshold": "> 1.00 = 溢價；≤ 1.00 = 折價背景，仍非單獨買進訊號",
            "current": f"{num(equity_mnav, 3)}x",
            "rationale": "市值除以扣債務、優先股與稅務楔子後的普通股淨值；倍率愈低，估值相對愈便宜。",
            "action": "高於 1.0 禁止宣稱普通股便宜；低於 1.0 仍須通過資料品質與資本結構覆核。",
        },
        {
            "metric": "企業價值／BTC 總值",
            "threshold": "< 1.00 = 企業價值低於 BTC 總值；> 1.00 = 資本飛輪可能仍可運作",
            "current": f"{num(enterprise_mnav, 3)}x",
            "rationale": "分子採普通股市值加債務與優先股、再扣現金；與普通股估值是不同問題。",
            "action": "只能判斷企業層溢價與融資飛輪；不可當作普通股便宜的證據。",
        },
        {
            "metric": "每週賣幣壓力倍數",
            "threshold": "> 2.00 is forced-sale pressure red flag",
            "current": f"{num(sale_ratio, 2)}x",
            "rationale": "Weekly BTC sales versus economic cushion can turn financing reflexivity from tailwind to headwind.",
            "action": "Block 2.5x tactical adds when above threshold.",
        },
        {
            "metric": "STRC 優先股折價信任票",
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
            "action": "只允許列入研究清單；行動前必須看到普通股估值、賣幣壓力、STRC 折價一起確認。",
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
            "hypothesis": "每週賣幣壓力倍數高於 2，代表融資壓力足以封鎖 2.5 倍 MSTR 小倉加碼。",
            "what_would_disprove": "連續多週賣幣壓力高於 2，但 STRC 折價收斂、現金覆蓋月數改善，且 MSTR 持續跑贏 BTC。",
            "required_evidence": "At least 4 weekly observations, preferred-market confirmation, and no verifier degradation.",
        },
        {
            "hypothesis": "STRC 優先股折價高於 5%，代表市場正在對資本結構投不信任票。",
            "what_would_disprove": "Discount remains wide for liquidity-only reasons while preferred coverage, issuance terms, and MSTR/BTC relative strength improve.",
            "required_evidence": "Bid/ask or volume context plus independent preferred-price source.",
        },
        {
            "hypothesis": "普通股市值／普通股淨值高於 1.0 時，MSTR 普通股相對自算淨值存在溢價。",
            "what_would_disprove": "Reviewed capital inputs show actual common shares, debt, preferred liquidation value, or deferred tax liabilities were materially overstated, or omitted assets were material.",
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
    btc_standard = snapshot.get("metrics", {}).get("btc_standard", {})
    bmnr_metrics = snapshot.get("metrics", {}).get("bmnr_metrics", {})
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
    common_valuation_gate_ok = metrics.get("common_valuation_gate_ok") is True
    capital_flywheel_gate_ok = metrics.get("capital_flywheel_gate_ok") is True
    verification_status = verification.get("status", "missing")
    analytics_confidence = quality.get("confidence", "missing")
    provenance_status = provenance.get("status", "missing")
    etf_flow_status = radar.get("etf_flow_status", "missing")
    state = str(decision.get("state", ""))
    headline = str(executive.get("headline", ""))
    one_line = str(executive.get("one_line", ""))
    tactical_read = str(executive.get("tactical_mstr_sleeve", ""))
    btc_dimensions = btc_standard.get("dimensions", {})
    bmnr_quality = bmnr_metrics.get("quality")
    expected_contract_red_light = bool(
        sale_ratio is None
        or sale_ratio > 2
        or coverage_months is None
        or coverage_months < 12
        or strc_discount is None
        or strc_discount > 0.05
    )

    blocked_actions = set()
    allowed_actions = {"research", "observe"}
    if contract_red_light or sale_ratio is None or sale_ratio > 2:
        blocked_actions.add("add_2_5x_mstr")
    if strc_discount is None or strc_discount > 0.05:
        blocked_actions.add("treat_mnav_as_green")
    if equity_mnav is None or equity_mnav > 1:
        blocked_actions.add("claim_common_equity_cheap")
    if not common_valuation_gate_ok:
        blocked_actions.add("common_valuation_green_light")
    if not capital_flywheel_gate_ok:
        blocked_actions.add("capital_flywheel_green_light")
    if verification_status != "pass" or provenance_status != "automated":
        blocked_actions.add("auto_trade")
    if etf_flow_status != "automated":
        blocked_actions.add("use_etf_flow_as_hard_trigger")
    if bmnr_quality != "net_to_common_reviewed":
        blocked_actions.add("claim_bmnr_net_nav_discount")

    invariants: list[dict[str, Any]] = []
    add_invariant(
        invariants,
        rule_id="NO_TACTICAL_ADD_WHEN_CONTRACT_RED_LIGHT",
        passed=(not contract_red_light) or (state == "block_leveraged_add" and "禁止" in tactical_read),
        evidence=f"contract_red_light={contract_red_light}, decision_state={state}, tactical_read={tactical_read}",
        risk_if_failed="False green light for the user's 2.5x leveraged MSTR tactical sleeve.",
    )
    add_invariant(
        invariants,
        rule_id="CONTRACT_RED_LIGHT_FAILS_CLOSED",
        passed=contract_red_light == expected_contract_red_light,
        evidence=f"actual={contract_red_light}, expected={expected_contract_red_light}, sale_ratio={num(sale_ratio, 2)}, coverage_months={num(coverage_months, 1)}, strc_discount={pct(strc_discount)}",
        risk_if_failed="Missing or adverse capital-stack inputs could silently open the leveraged-trade gate.",
    )
    add_invariant(
        invariants,
        rule_id="DEGRADED_DATA_CANNOT_BE_AUTO_TRADING_GRADE",
        passed=(verification_status == "pass") or ("auto_trade" in blocked_actions and analytics_confidence != "high"),
        evidence=f"verification={verification_status}, headline={headline}, confidence={analytics_confidence}",
        risk_if_failed="Research-grade data could be mistaken for execution-grade signal.",
    )
    add_invariant(
        invariants,
        rule_id="COMMON_PRICE_TO_NAV_ABOVE_ONE_BLOCKS_CHEAPNESS_CLAIM",
        passed=(equity_mnav is not None and equity_mnav <= 1) or "cheap" not in (headline + " " + one_line).lower(),
        evidence=f"common_equity_price_to_nav={num(equity_mnav, 3)}x, headline={headline}",
        risk_if_failed="Common equity may be called cheap while it trades above adjusted net value to common.",
    )
    add_invariant(
        invariants,
        rule_id="COMMON_VALUATION_GATE_DIRECTION",
        passed=common_valuation_gate_ok == ((equity_mnav is not None and equity_mnav <= 1) and not pref_dilution_flag),
        evidence=f"common_valuation_gate_ok={common_valuation_gate_ok}, common_equity_price_to_nav={num(equity_mnav, 3)}x, preferred_distortion_flag={pref_dilution_flag}",
        risk_if_failed="A premium above common NAV could be mislabeled as valuation safety.",
    )
    add_invariant(
        invariants,
        rule_id="CAPITAL_FLYWHEEL_GATE_SEPARATE_FROM_VALUATION",
        passed=capital_flywheel_gate_ok == ((equity_mnav is not None and equity_mnav >= 1) and (enterprise_mnav is not None and enterprise_mnav >= 1) and not pref_dilution_flag),
        evidence=f"capital_flywheel_gate_ok={capital_flywheel_gate_ok}, common_price_to_nav={num(equity_mnav, 3)}x, enterprise_value_to_btc_nav={num(enterprise_mnav, 3)}x",
        risk_if_failed="Financing capacity and investor valuation could be collapsed into one misleading green light.",
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
        passed=(strc_discount is not None and strc_discount <= 0.05) or contract_red_light,
        evidence=f"strc_discount={pct(strc_discount)}, contract_red_light={contract_red_light}",
        risk_if_failed="Headline mNAV could ignore preferred-market capital-stack stress.",
    )
    add_invariant(
        invariants,
        rule_id="SALE_PRESSURE_BLOCKS_LEVERAGED_ADD",
        passed=(sale_ratio is not None and sale_ratio <= 2) or contract_red_light,
        evidence=f"sale_ratio={num(sale_ratio, 2)}x, contract_red_light={contract_red_light}",
        risk_if_failed="Forced-sale pressure could be treated as a dip-buy setup.",
    )
    add_invariant(
        invariants,
        rule_id="MANUAL_PROVENANCE_FORCES_DEGRADED_STATUS",
        passed=(provenance_status == "automated") or verification_status in {"degraded", "fail"},
        evidence=f"manual_input_provenance={provenance_status}, verification={verification_status}; pass means false-green-light is blocked, not that inputs are fully automated",
        risk_if_failed="Manual capital-stack seeds could be displayed as fully verified data.",
    )
    add_invariant(
        invariants,
        rule_id="ETF_FLOW_NOT_HARD_TRIGGER_UNTIL_AUTOMATED",
        passed=(etf_flow_status == "automated") or btc_standard.get("data_quality", {}).get("etf_flow_counts_as_confirmation") is False,
        evidence=f"etf_flow_status={etf_flow_status}, counts_as_confirmation={btc_standard.get('data_quality', {}).get('etf_flow_counts_as_confirmation')}",
        risk_if_failed="Missing ETF-flow automation could still influence hard trading decisions.",
        severity="major",
    )
    add_invariant(
        invariants,
        rule_id="RESERVE_COVERAGE_ESCALATES_BELOW_ONE_YEAR",
        passed=(coverage_months is not None and coverage_months >= 12) or contract_red_light,
        evidence=f"coverage_months={num(coverage_months, 1)}, contract_red_light={contract_red_light}",
        risk_if_failed="Liquidity runway deterioration could be missed in leveraged sizing.",
        severity="major",
    )
    add_invariant(
        invariants,
        rule_id="BTC_REGIME_EXCLUDES_VEHICLE_RISK",
        passed="MSTR 資本壓力" not in btc_dimensions and "BMNR 資本壓力" not in btc_dimensions,
        evidence=f"btc_dimensions={sorted(btc_dimensions)}",
        risk_if_failed="MSTR/BMNR capital-structure stress could distort the BTC market regime score.",
    )
    add_invariant(
        invariants,
        rule_id="BMNR_GROSS_ASSETS_NOT_NET_NAV",
        passed=(bmnr_quality == "net_to_common_reviewed") or "claim_bmnr_net_nav_discount" in blocked_actions,
        evidence=f"bmnr_quality={bmnr_quality}, blocked_actions={sorted(blocked_actions)}",
        risk_if_failed="Gross crypto and cash holdings could be mislabeled as value attributable to common equity.",
    )

    contradictions: list[dict[str, Any]] = []
    if contract_red_light and any(word in state for word in ["加碼", "做多", "買進"]) and "禁止" not in state:
        contradictions.append({"id": "STATE_CONTRADICTS_CONTRACT_RED_LIGHT", "evidence": state})
    if verification_status != "pass" and any(word in headline.lower() for word in ["auto-trading grade", "green light", "buy"]):
        if "not auto-trading" not in headline.lower():
            contradictions.append({"id": "HEADLINE_OVERSTATES_DEGRADED_DATA", "evidence": headline})
    if equity_mnav is not None and equity_mnav > 1 and "cheap" in (headline + " " + one_line).lower():
        contradictions.append({"id": "CHEAPNESS_LANGUAGE_WITH_COMMON_PREMIUM", "evidence": headline + " / " + one_line})
    if common_valuation_gate_ok and (equity_mnav is None or equity_mnav > 1 or pref_dilution_flag):
        contradictions.append({"id": "COMMON_VALUATION_GATE_CONTRADICTS_COMPONENTS", "evidence": f"gate={common_valuation_gate_ok}, 普通股市值／淨值={num(equity_mnav, 3)}, 特別股融資扭曲旗標={pref_dilution_flag}"})
    if analytics.get("decomposition", {}).get("mstr_return_1d") and (equity_mnav is not None and equity_mnav > 1 or sale_ratio is None or sale_ratio > 2 or strc_discount is None or strc_discount > 0.05):
        mstr_return = n(analytics.get("decomposition", {}).get("mstr_return_1d"))
        valuation_blocks = {"common_valuation_green_light", "treat_mnav_as_green"} & blocked_actions
        if mstr_return is not None and mstr_return > 0 and not valuation_blocks:
            contradictions.append({"id": "PRICE_REBOUND_WITHOUT_STRUCTURE_REPAIR", "evidence": f"mstr_return_1d={pct(mstr_return)}, 普通股市值／淨值={num(equity_mnav, 3)}, 每週賣幣壓力倍數={num(sale_ratio, 2)}, STRC 優先股折價={pct(strc_discount)}"})
    if bmnr_quality != "net_to_common_reviewed" and any(term in (headline + " " + one_line).lower() for term in ["bmnr net nav", "bmnr 淨值折價", "bmnr 普通股安全邊際"]):
        contradictions.append({"id": "BMNR_GROSS_ASSETS_MISLABELED_AS_NET_NAV", "evidence": headline + " / " + one_line})

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
            "plain_english": "BTC 市場狀態、MSTR 合約紅燈與 BMNR gross-asset 折價分開判斷；資料降級或載具紅旗存在時，只允許研究與觀察。",
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
